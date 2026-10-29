from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
from io import BytesIO

from byteplussdkarkruntime import Ark

from content_creator.services.characters import _extract_output_text
from content_creator.services.model_settings import prompt_model

# The Film Script to Seedance skill (shot prompt format, timing, dialogue rules) lives in
# skills/film-script-to-seedance/SKILL.md so it can be edited without touching code.
SKILL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "skills", "film-script-to-seedance", "SKILL.md")
with open(SKILL_PATH, "r", encoding="utf-8") as _f:
    SKILL_CONTEXT = _f.read().strip()


def extract_text_from_pdf_bytes(raw_bytes: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("PDF upload requires the `pypdf` package to be installed.") from exc

    reader = PdfReader(BytesIO(raw_bytes))
    pages: list[str] = []
    for page in reader.pages:
        text = (page.extract_text() or "").strip()
        if text:
            pages.append(text)
    return "\n\n".join(pages).strip()


# The longest shot one Seedance 2.5 generation renders. Shots are packed up to this length
# so a script costs as few generations as possible.
MAX_SHOT_SECONDS = 30
# Product guidance: all dialogue in a 30 s video combined is best at 30–40 words. More than
# that and lines get rushed or cut; the budget scales with a shorter shot's length.
MAX_DIALOGUE_WORDS = 40




def _strip_fences(text: str) -> str:
    out = (text or "").strip()
    if out.startswith("```"):
        lines = out.splitlines()
        out = "\n".join(lines[1:-1] if lines and lines[-1].strip() == "```" else lines[1:]).strip()
    return out


def _ask(system: str, user: str, temperature: float = 0.2, max_tokens: int = 12000, think: bool = True) -> str:
    # think=False switches the model's reasoning off: mechanical tasks (reading a script into
    # elements) are faster and cannot spend the whole output budget deliberating.
    resp = Ark().responses.create(
        model=prompt_model(),
        input=[
            {"role": "system", "content": system},
            {"role": "user", "content": [{"type": "input_text", "text": user}]},
        ],
        temperature=temperature,
        max_output_tokens=max_tokens,
        **({} if think else {"extra_body": {"thinking": {"type": "disabled"}}}),
    )
    return _strip_fences(_extract_output_text(resp))


# ---- Timing and packing, done in code: deterministic, instant, and the same every run ----
# A scene heading: "INT. …", "12. EXT. …", "SCENE 1 — INT. …", "Scene 3: EXT …".
_HEADING = re.compile(r"^\s*(?:(?:scene\s+)?\d+[A-Z]?\s*[.):—–-]*\s*)?(?:INT\.?|EXT\.?|INT\./EXT\.?|I/E\.?)[\s.]", re.I)
# Dialogue on one line without a colon: "INGRID (flat) Falkenrath." / "OYELARAN You got here fast…"
# — a capitalised name (optionally CONT'D / V.O. / a parenthetical), then the spoken words starting
# with a capital, digit or quote. An action line with a name continues in lower case ("PRIYA hovers").
_SAME_LINE = re.compile(
    r"^\s*((?:[A-Z][A-Z'’.\-]+)(?:\s+[A-Z][A-Z'’.\-]+){0,3})\s*((?:\([^)]*\)\s*)*)([A-Z0-9\"“'‘¿¡…].*)$")
_CUE = re.compile(r"^\s*([A-Z][A-Z0-9 .'\-]{1,40}?)\s*(\((?:CONT'D|CONT|V\.O\.|O\.S\.|O\.C\.)[^)]*\))?\s*$")
_INLINE = re.compile(r"^\s*([A-Z][A-Za-z0-9 .'\-]{1,40}?)\s*(?:\([^)]*\))?\s*:\s*(.+)$")
# Screen time: speech at a natural ~2.5 words/s plus a short pause, ~2 s per action sentence.
# The 30–40-words-per-30 s density comes from the MAX_DIALOGUE_WORDS cap, not from slowing speech.
WORDS_PER_SECOND = 2.5
SPEECH_PAUSE_SECONDS = 1.0
SECONDS_PER_SENTENCE = 2.0


def _words(text: str) -> int:
    return len(re.findall(r"[\w'’]+", text))


# Page furniture from PDFs and screenplay software: never story content.
_FURNITURE = re.compile(
    r"^\s*(?:page\s+\d+(?:\s+of\s+\d+)?|\d+\s*(?:/|of)\s*\d+|\d{1,3}\.?|\(?\s*more\s*\)?|\(?\s*cont(?:inued|'d)?\s*\)?:?|"
    r"(?:fade|cut|smash cut|dissolve)\s+(?:in|out|to)[:.]?|the end\.?)\s*$", re.I)


def _unwrap(lines: list) -> list:
    """Re-joins lines a PDF wrapped mid-sentence: a line that continues in lower case belongs
    to the line above (unless that one is a heading or a lone speaker cue)."""
    out = []
    for ln in lines:
        t = ln.strip()
        if out and t and t[0].islower() and out[-1].strip() and not _HEADING.match(out[-1].strip()) \
                and not _CUE.match(out[-1].strip()):
            out[-1] = out[-1].rstrip() + " " + t
        else:
            out.append(ln)
    return out


def story_text(script_text: str) -> str:
    """Only the screenplay itself: front matter before the first scene heading (title page,
    logline, character list, notes) and page furniture (page numbers, MORE / CONTINUED, FADE IN)
    are dropped. Without any scene heading the whole text is the story, minus page furniture."""
    lines = script_text.replace("\r", "").split("\n")
    title = next((ln.strip() for ln in lines if ln.strip()), "")
    first = next((k for k, ln in enumerate(lines) if _HEADING.match(ln.strip())), None)
    if first is not None:
        lines = lines[first:]
    # Page furniture, and the title repeated as a running header on every page.
    kept = [ln for ln in lines if not _FURNITURE.match(ln.strip()) and not (title and ln.strip() == title)]
    return "\n".join(_unwrap(kept))


def _script_units(script_text: str) -> list:
    """The script as timed units in order: scene headings, action lines and whole speeches
    (a speaker cue + its line(s) is one unit, so dialogue is never split)."""
    lines = [ln.rstrip() for ln in script_text.replace("\r", "").split("\n")]
    units, i = [], 0
    while i < len(lines):
        ln = lines[i].strip()
        i += 1
        if not ln:
            continue
        if _HEADING.match(ln):
            units.append({"kind": "heading", "text": ln, "seconds": 0.0, "words": 0})
            continue
        cue = _CUE.match(ln)
        if cue and not ln.endswith(".") and len(ln.split()) <= 5 and i < len(lines):
            speech = [ln]
            spoken = []
            while i < len(lines) and lines[i].strip():
                nxt = lines[i].strip()
                if nxt.startswith("("):            # a parenthetical belongs to the speech
                    speech.append(nxt); i += 1; continue
                if spoken and (_CUE.match(nxt) or _HEADING.match(nxt)):
                    break
                speech.append(nxt); spoken.append(nxt); i += 1
                # Screenplay dialogue is one block; a following sentence-case line that reads
                # like action (no quote marks, starts after a full stop) ends the speech.
                if i < len(lines) and lines[i].strip() and not lines[i].strip().startswith("(") \
                        and not lines[i].startswith((" ", "\t")):
                    break
            if spoken:
                w = _words(" ".join(spoken))
                units.append({"kind": "speech", "text": "\n".join(speech), "words": w,
                              "seconds": w / WORDS_PER_SECOND + SPEECH_PAUSE_SECONDS})
                continue
        inline = _INLINE.match(ln)
        same = None if inline else _SAME_LINE.match(ln)
        if (inline or same) and not _HEADING.match(ln):
            spoken = inline.group(2) if inline else same.group(3)
            w = _words(spoken)
            units.append({"kind": "speech", "text": ln, "words": w, "seconds": w / WORDS_PER_SECOND + SPEECH_PAUSE_SECONDS})
            continue
        sentences = max(1, len(re.findall(r"[.!?]+(?:\s|$)", ln)))
        units.append({"kind": "action", "text": ln, "words": 0,
                      "seconds": max(1.5, sentences * SECONDS_PER_SENTENCE)})
    return units


# ---- Reading any script layout: the model tags each element, code does the arithmetic ----
STRUCTURE_SYSTEM = """You read one chunk of a script (any layout: screenplay, PDF export, stage play, prose treatment, ad script, two-column AV script, bullet outline) and tag every element in reading order. The lines are numbered L1, L2, …

Element types:
- "heading": a scene / location / time change (sluglines, "SCENE 3", "Location: …", "CUT TO the rooftop").
- "action": something the camera shows — description, movement, a reaction, a pause or silence that is meant to be seen ("A long beat", "Rain falls").
- "dialogue": words a character speaks aloud, including voice-over. "speaker" = who speaks; "text" = ONLY the spoken words, without the name or the stage direction; "delivery" = any direction like (quiet) or (to herself).
- "onscreen": text the script explicitly asks to be shown in the video (SUPER:, TITLE:, CAPTION:, "text on screen", a text message shown on a phone).
- "skip": NOT part of the film — title pages, author/draft info, loglines, synopses, character lists and descriptions, notes, page numbers, running headers/footers, "(MORE)", "CONTINUED", "FADE IN/OUT", "CUT TO:", "THE END".

For "action" and "onscreen" give "seconds": how long it realistically takes on screen (a glance 1, a short movement 2–3, crossing a room 3–4, an establishing view 3–5, an explicit long silence 3–5). Keep it tight — never pad.
Copy text VERBATIM (you may re-join a sentence that a PDF broke across lines). Never invent, summarise or drop story content. A line can hold several elements (e.g. a speaker name and the line in one line) — split them.

Return ONLY JSON, no prose: {"elements":[{"type":"…","speaker":"…","delivery":"…","text":"…","seconds":0}]}"""

CHUNK_LINES = 90


def _chunks(lines: list) -> list:
    """Splits the script into chunks of about CHUNK_LINES, preferring to cut before a scene heading
    or at a blank line so an element is rarely split."""
    chunks, start = [], 0
    while start < len(lines):
        end = min(len(lines), start + CHUNK_LINES)
        if end < len(lines):
            window = range(end, max(start + CHUNK_LINES // 2, end - 25), -1)
            cut = next((k for k in window if _HEADING.match(lines[k].strip()) or not lines[k].strip()), end)
            end = cut
        chunks.append(lines[start:end])
        start = end
    return chunks


def _speech_seconds(words: int) -> float:
    return words / WORDS_PER_SECOND + SPEECH_PAUSE_SECONDS


def structure_script(script_text: str) -> list:
    """The script as timed elements, whatever its layout. The model tags elements per chunk (in
    parallel, thinking off); dialogue time is computed from its words here."""
    lines = _unwrap(script_text.replace("\r", "").split("\n"))
    chunks = _chunks(lines)
    offsets = [sum(len(c) for c in chunks[:k]) for k in range(len(chunks))]

    def tag(k: int) -> list:
        numbered = "\n".join(f"L{offsets[k] + n + 1}: {ln}" for n, ln in enumerate(chunks[k]))
        where = "the START of the script" if k == 0 else f"chunk {k + 1} of {len(chunks)} (the story continues from an earlier chunk)"
        for _ in range(2):
            try:
                text = _ask(STRUCTURE_SYSTEM, f"This is {where}.\n\n{numbered}\n\nReturn the JSON.", 0.0,
                            max_tokens=16000, think=False)
                data = json.loads(text[text.find("{"): text.rfind("}") + 1])
                return [e for e in data.get("elements") or [] if isinstance(e, dict)]
            except (ValueError, AttributeError):
                continue
        raise RuntimeError("could not read a part of the script")

    with ThreadPoolExecutor(max_workers=8) as pool:
        tagged = list(pool.map(tag, range(len(chunks))))

    units = []
    for el in (e for part in tagged for e in part):
        kind = str(el.get("type") or "").lower()
        text = str(el.get("text") or "").strip()
        # On-screen text (SUPERs, captions, titles) is never generated, so it takes no screen time.
        if kind in ("skip", "onscreen") or not text:
            continue
        if kind == "heading":
            units.append({"kind": "heading", "text": text, "seconds": 0.0, "words": 0})
        elif kind == "dialogue":
            who = str(el.get("speaker") or "").strip() or "SPEAKER"
            how = str(el.get("delivery") or "").strip().strip("()")
            label = f"{who.upper()}{f' ({how})' if how else ''}"
            # A speech longer than the shot budget is split at sentence ends, same speaker.
            sentences = re.split(r"(?<=[.!?…])\s+", text)
            part = []
            for sent in sentences + [None]:
                if sent is not None and _words(" ".join(part + [sent])) <= MAX_DIALOGUE_WORDS:
                    part.append(sent)
                    continue
                if part:
                    said = " ".join(part)
                    units.append({"kind": "speech", "text": f'{label}: "{said}"', "words": _words(said),
                                  "seconds": _speech_seconds(_words(said))})
                part = [sent] if sent is not None else []
        else:
            try:
                secs = float(el.get("seconds") or 0)
            except (TypeError, ValueError):
                secs = 0
            if secs <= 0:
                secs = max(1.5, len(re.findall(r"[.!?]+(?:\s|$)", text)) * SECONDS_PER_SENTENCE)
            units.append({"kind": "action", "text": text, "words": 0, "seconds": max(1.0, min(10.0, secs))})
    return units


def plan_script_shots(script_text: str) -> dict:
    """Packs the script into shots, each filled as full as it can be — up to MAX_SHOT_SECONDS and
    MAX_DIALOGUE_WORDS — before the next one starts. Content is pulled forward into an unfilled
    shot even across a scene change (it becomes a hard cut inside the shot), so no shot is padded
    with silence and the script costs the fewest generations. A short shot happens only at the
    very end, or where the dialogue budget is full."""
    try:
        units = structure_script(script_text)
        if not any(u["seconds"] for u in units):
            raise RuntimeError("nothing timed")
    except Exception:
        # Fallback: the rule-based reader (screenplay-like layouts).
        units = _script_units(story_text(script_text))
    if not any(u["seconds"] for u in units):
        raise RuntimeError("Could not find any action or dialogue in the script.")

    groups, cur, secs, words = [], [], 0.0, 0
    for u in units:
        fits = secs + u["seconds"] <= MAX_SHOT_SECONDS and words + u["words"] <= MAX_DIALOGUE_WORDS
        if cur and u["seconds"] and not fits:
            groups.append(cur)
            cur, secs, words = [], 0.0, 0
        cur.append(u)
        secs += u["seconds"]
        words += u["words"]
    if cur:
        groups.append(cur)
    # A short leftover at the very end folds into the shot before it when that nearly fits
    # (its beats are compressed slightly) — a separate few-second generation is wasted money.
    if len(groups) > 1:
        tail, prev = groups[-1], groups[-2]
        t_secs, p_secs = sum(u["seconds"] for u in tail), sum(u["seconds"] for u in prev)
        t_words, p_words = sum(u["words"] for u in tail), sum(u["words"] for u in prev)
        if t_secs < 8 and p_secs + t_secs <= MAX_SHOT_SECONDS + 6 and p_words + t_words <= MAX_DIALOGUE_WORDS:
            groups[-2:] = [prev + tail]
    # A heading that closed a group belongs to the next one.
    for k in range(len(groups) - 1):
        while groups[k] and groups[k][-1]["kind"] == "heading":
            groups[k + 1].insert(0, groups[k].pop())

    shots = []
    for part in groups:
        total = sum(u["seconds"] for u in part)
        if total <= 0:
            continue
        secs = max(4, min(MAX_SHOT_SECONDS, int(-(-total // 1))))
        scale = secs / total
        beats, t = [], 0.0
        for u in part:
            if u["kind"] == "heading":
                # Mid-shot: a hard cut to the new place. At the start: just where the shot is set.
                label = "[NEW SCENE — hard cut]" if t > 0 else "[SCENE]"
                beats.append({"start": round(t * scale), "end": round(t * scale), "what": f"{label} {u['text']}"})
                continue
            beats.append({"start": round(t * scale), "end": round((t + u["seconds"]) * scale), "what": u["text"].replace("\n", " / ")})
            t += u["seconds"]
        shots.append({"seconds": secs, "words": int(sum(u["words"] for u in part)),
                      "span": "\n".join(u["text"] for u in part), "beats": beats})
    return {"total_seconds": int(round(sum(u["seconds"] for u in units))), "shots": shots,
            "story": "\n".join(sh["span"] for sh in shots)}


def _shot_system() -> str:
    return (
        "You are converting ONE planned shot of a script into a production-ready Seedance prompt.\n\n"
        "Use the following local skill document as the source of truth:\n\n"
        f"{SKILL_CONTEXT}\n\n"
        "Additional implementation rules (these OVERRIDE the skill document wherever they differ — in particular "
        "its REFERENCES block and [Image N] tags are NOT used here):\n"
        "- Reference images (character sheets, location plates, props) are attached LATER, at generation time, "
        "and bound to the prompt with lines like `@Image2 is Annelise Voss`. So: never write [Image N] or @Image tags, "
        "never write a REFERENCES block, and never describe a character's, location's or prop's APPEARANCE "
        "(face, hair, age, clothing, colours, materials) — that comes from the images, and a second description "
        "competes with them.\n"
        "- Start with one line `IN THIS SHOT:` listing, by name only and exactly as named in the script, the "
        "characters who appear, the location, and the key props. List ONLY who and what is on screen in this shot.\n"
        "- Then SHOT DESCRIPTION, CINEMATOGRAPHY and AUDIO blocks, in that order.\n"
        "- SHOT DESCRIPTION must FILL the shot's whole runtime with dense, concrete direction, written as timed "
        "beats in order, e.g. `0–4s: …`, `4–9s: …`, through to the last second. Every beat states the camera "
        "(setup, framing, movement, or a hard cut to a new setup), the blocking and physical action, eyelines and "
        "micro-expressions, and environmental motion. The PLANNED BEATS are already timed to fill the runtime: "
        "follow their order and timing, and never stretch a moment with holds, silences or 'camera stays locked' "
        "filler beyond what a beat states. A `[NEW SCENE — hard cut]` beat means a hard cut to that new location "
        "at that moment, inside this same shot; a `[SCENE]` beat only says where the shot is set (do not write a 0-second beat for it).\n"
        "- Dialogue pacing: about 30–40 words of dialogue in total suit a 30 s shot. Give each line room — a beat "
        "of reaction or action between lines — so no line is rushed.\n"
        "- Every line of dialogue from the span, word for word, in bold quotes with the speaker named and the "
        "delivery noted. Never invent, drop or paraphrase a line.\n"
        "- Cover ONLY this shot's span: do not show events from the previous or next shot.\n"
        "- NO ON-SCREEN TEXT of any kind: no titles, SUPERs, captions, subtitles, lower thirds, logos or "
        "written words in the frame — even if the script asks for them. A phone message or sign that matters "
        "to the story is shown by the character's reaction or read aloud, never as readable text.\n"
        "- End SHOT DESCRIPTION with `{seconds} second total runtime.` using this shot's runtime.\n"
        "- Return plain text only. No code fences, no SHOT heading (it is added for you)."
    )


def _write_shot(script: str, plan: dict, index: int) -> str:
    shot = plan["shots"][index]
    prev_span = plan["shots"][index - 1]["span"] if index > 0 else "(none — this opens the script)"
    next_span = plan["shots"][index + 1]["span"] if index + 1 < len(plan["shots"]) else "(none — this ends the script)"
    beats = "\n".join(f"- {b.get('start', '?')}–{b.get('end', '?')}s: {b.get('what')}" for b in shot["beats"]) or "(plan your own beats)"
    user = (
        f'THE WHOLE STORY (context only):\n"""\n{plan.get("story", "")[:30000]}\n"""\n\n'
        f"THIS IS SHOT {index + 1} OF {len(plan['shots'])}. RUNTIME: {shot['seconds']} seconds.\n"
        f'ITS SPAN (cover exactly this):\n"""\n{shot["span"]}\n"""\n'
        f"PLANNED BEATS:\n{beats}\n\n"
        f'Previous shot covered: """{prev_span[:600]}"""\nNext shot covers: """{next_span[:600]}"""\n\n'
        "Write this shot's prompt."
    )
    return _ask(_shot_system().replace("{seconds}", str(shot["seconds"])), user, 0.3, max_tokens=32000)


def generate_seedance_prompts_from_script(script_text: str) -> str:
    """Script -> the fewest shots of up to 30 s (a timing pass), then one dense prompt per shot (in parallel)."""
    cleaned_script = script_text.strip()
    if not cleaned_script:
        raise RuntimeError("No script text provided.")
    plan = plan_script_shots(cleaned_script)
    with ThreadPoolExecutor(max_workers=6) as pool:
        bodies = list(pool.map(lambda i: _write_shot(cleaned_script, plan, i), range(len(plan["shots"]))))
    # A bare "SHOT N" heading line is what the page splits shots on; the runtime is in each shot's text.
    return "\n\n".join(f"SHOT {i + 1}\n\n{body.strip()}" for i, body in enumerate(bodies))


CHARACTER_SHEET_SYSTEM = """You are a film's costume and casting department. From the script, list every character who appears on screen and recurs or matters (skip unseen voices and one-line extras), and write each one's CHARACTER SHEET DESCRIPTION: the text an image model will use to draw a four-view reference sheet (face close-up, front, side, back) of that character.

Each description covers, in this order and as concrete visual facts:
1. Who: gender, age, build, height impression, skin tone and texture.
2. Face and hair: face shape, eyes, brows, nose, mouth, facial hair, marks (scars, freckles, tattoos), hair colour, length and style.
3. Outfit, every garment from the outside in: colour, material, fit, condition, how it fastens, what is worn on top where garments overlap, and what the BACK of each garment looks like.
4. Footwear and accessories, and for every one-sided item which side it is on, from the character's own perspective (e.g. "a leather satchel over her left shoulder").
5. Style: photorealistic unless the script states another medium.
Take everything the script states, verbatim where possible; where the script is silent, make ONE specific choice that fits the character, era, place and tone — never leave a blank or write "unspecified". No posture, expression, lighting, setting or action — a sheet shows the character neutral on a plain background.

Return ONLY JSON — no prose, no code fences:
{"characters":[{"name":"<as written in the script>","role":"<one short line: who they are in the story>","description":"<the sheet description, 80–160 words, plain prose>"}]}"""


def generate_character_sheet_prompts(script_text: str) -> list:
    """One Character Sheets description per on-screen character in the script."""
    cleaned = script_text.strip()
    if not cleaned:
        return []
    ark = Ark()
    resp = ark.responses.create(
        model=prompt_model(),
        input=[
            {"role": "system", "content": CHARACTER_SHEET_SYSTEM},
            {"role": "user", "content": [{"type": "input_text", "text": f"SCRIPT:\n\"\"\"\n{cleaned[:30000]}\n\"\"\"\n\nReturn the JSON."}]},
        ],
        temperature=0.3,
    )
    text = _extract_output_text(resp).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        data = json.loads(text[text.find("{"): text.rfind("}") + 1])
    except ValueError:
        return []
    out = []
    for c in data.get("characters") or []:
        if isinstance(c, dict) and str(c.get("description") or "").strip():
            out.append({"name": str(c.get("name") or "Character").strip()[:60],
                        "role": str(c.get("role") or "").strip()[:200],
                        "description": str(c.get("description")).strip()})
    return out


LOCATION_SYSTEM = """You are a film's location scout and production designer. From the script, list every distinct LOCATION a scene takes place in (merge repeated sluglines for the same place; skip places only mentioned, never shown). For each, write the DIRECTION for a location-variations tool, which first builds one empty plate of the place and then renders coverage of it.

Each direction has two parts in one paragraph:
1. THE PLACE, as concrete visual facts: interior or exterior, era and region, architecture and layout, materials and surfaces, key set pieces and props the scenes use, signage or dressing, scale. Take what the script states; where it is silent make ONE specific choice that fits the story, period and tone. No people or animals — the plate is empty.
2. "Explore:" followed by 3–6 variations the script actually needs: the camera angles its scenes imply (e.g. a low wide from the doorway, a high angle over the counter), and every time of day, weather or state the script puts this place in (night, rain, after the fire…).

Return ONLY JSON — no prose, no code fences:
{"locations":[{"name":"<short name, e.g. TEA STALL>","scenes":"<which scenes / sluglines use it>","direction":"<the direction, 70–150 words>"}]}"""


def generate_location_prompts(script_text: str) -> list:
    """One Location Variations direction per location in the script."""
    cleaned = script_text.strip()
    if not cleaned:
        return []
    ark = Ark()
    resp = ark.responses.create(
        model=prompt_model(),
        input=[
            {"role": "system", "content": LOCATION_SYSTEM},
            {"role": "user", "content": [{"type": "input_text", "text": f"SCRIPT:\n\"\"\"\n{cleaned[:30000]}\n\"\"\"\n\nReturn the JSON."}]},
        ],
        temperature=0.3,
    )
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", _extract_output_text(resp).strip())
    try:
        data = json.loads(text[text.find("{"): text.rfind("}") + 1])
    except ValueError:
        return []
    out = []
    for loc in data.get("locations") or []:
        if isinstance(loc, dict) and str(loc.get("direction") or "").strip():
            out.append({"name": str(loc.get("name") or "Location").strip()[:60],
                        "scenes": str(loc.get("scenes") or "").strip()[:200],
                        "direction": str(loc.get("direction")).strip()})
    return out
