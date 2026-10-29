"""Redub prompt optimisation with the Seed Audio 1.5 prompt skill (skills/seedaudio-prompt-optimizer).

The raw redub prompt is the pre-prompt, the voice bindings and every shot's complete Seedance prompt.
That carries the dialogue, ambience, foley and music cues Seed Audio should produce in one pass, but it
also carries camera, lens and grade text, repeats every line (SHOT DESCRIPTION and AUDIO), and passes
Seed Audio's 10,000-character prompt limit after two or three shots. Seed LLM, following the skill,
rewrites it into one sound script: lines verbatim, voices bound, music/ambience/effects in event order,
placed in the combined video's time using each shot's offset.
"""

import os
import re

from content_creator.services.script_to_prompt import _ask

SKILL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "skills", "seedaudio-prompt-optimizer")
PROMPT_LIMIT = 10000   # Seed Audio 1.5 provisional prompt ceiling (skill: references/api-rules.md)
TARGET_CHARS = 9000    # what the optimiser aims for, leaving headroom under the limit
MAX_VIDEO_SECONDS = 360


def skill_text() -> str:
    """SKILL.md plus its reference files, as one system prompt."""
    parts = []
    for rel in ("SKILL.md", "references/api-rules.md", "references/sound-writing-guide.md", "references/troubleshooting.md"):
        with open(os.path.join(SKILL_DIR, rel), "r", encoding="utf-8") as f:
            parts.append(f"===== {rel} =====\n{f.read().strip()}")
    return "\n\n".join(parts)


def optimize(raw_prompt: str, shots: list, voices: list, duration: float, progress) -> dict:
    """shots: [{"label", "start", "end"}] in the combined video; voices: [{"name", "ref"}] in @Audio order."""
    if not raw_prompt.strip():
        raise ValueError("The redub prompt is empty.")
    timing = "\n".join(f"- Shot {s['label']}: {s['start']:.1f}s–{s['end']:.1f}s of the video" for s in shots)
    cast = "\n".join(f"- @音频{i + 1} is the voice reference for {v['name']}" for i, v in enumerate(voices)) or "- none (keep each character's original voice from the video)"
    user = f"""Optimize the prompt below for a SeedAudio video-dubbing request.

Assets supplied with the request:
- One reference video, {duration:.1f} seconds long: the cut made by joining the shots below in order. Its existing audio has pronunciation errors; the new audio replaces it entirely.
- Voice reference audio, in this order:
{cast}

Where each shot sits in the video (user-supplied timing). Every beat time in the shot texts is already in this video's time — one continuous timeline, not restarting per shot:
{timing}

The prompt has the user's dubbing instruction first, then the voice bindings, then each shot's video-generation prompt in order (camera/lens blocks already removed). Treat the shot prompts as a visual script and convert them into one sound script that produces, in a single pass: every spoken line verbatim with its speaker, the music, the ambience and the sound effects they describe. Keep the dubbing instruction's requirements (exact dialogue, corrected pronunciation, distinct voices, timing and lip sync). Each spoken line must appear exactly once even when a shot prompt repeats it. Keep the result under {TARGET_CHARS} characters.

PROMPT:
\"\"\"
{raw_prompt.strip()}
\"\"\""""
    # The skill's reference files are full of Chinese examples; without this the model sometimes writes
    # an English request's descriptions in Chinese. Only the @音频N tags are fixed tokens.
    language = instruction_language(raw_prompt)
    user += (f"\n\nWrite every description in {language} — the language of the dubbing instruction. "
             "Spoken lines stay exactly as written; only the @音频N voice tags are in their fixed form.")
    progress("Seed LLM is applying the Seed Audio 1.5 prompt skill")
    out = _ask(skill_text(), user, temperature=0.2, max_tokens=16000).strip()
    if language == "English" and len(re.findall(r"[\u4e00-\u9fff]", out.replace("@音频", ""))) > 20:
        progress("Rewriting descriptions in English")
        redo = _ask(skill_text(), user + "\n\nYour last answer wrote descriptions in Chinese. Write them in English.", temperature=0.2, max_tokens=16000).strip()
        if redo:
            out = redo
    if not out:
        raise RuntimeError("The optimiser returned an empty prompt.")
    if len(out) > PROMPT_LIMIT:
        # Ten 30 s shots land near the limit; one tightening pass keeps lines and sounds, trims wording.
        progress(f"Tightening ({len(out)} characters, limit {PROMPT_LIMIT})")
        shorter = _ask(skill_text(), f"""This SeedAudio prompt is {len(out)} characters; it must be under {TARGET_CHARS}. Shorten it by trimming descriptive wording and merging repeated ambience — keep every spoken line verbatim with its speaker and @音频 binding, every time interval, and every distinct sound event. Output only the prompt.

PROMPT:
\"\"\"
{out}
\"\"\"""", temperature=0.2, max_tokens=16000).strip()
        if shorter and len(shorter) < len(out) and not missing_lines(raw_prompt, shorter):
            out = shorter
    lost = missing_lines(raw_prompt, out)
    if lost:
        # Long episodes can come back with a line paraphrased or merged; give the exact lines back once.
        progress(f"Restoring {len(lost)} line(s) word for word")
        exact = "\n".join(f'- "{line}"' for line in lost)
        fixed = _ask(skill_text(), f"""In this SeedAudio prompt, these spoken lines are not word for word as written in the script. Restore each one exactly as given below — same words, same punctuation — in its place, with its speaker and @音频 binding. Change nothing else. Output only the prompt.

EXACT LINES:
{exact}

PROMPT:
\"\"\"
{out}
\"\"\"""", temperature=0.1, max_tokens=16000).strip()
        if fixed and len(missing_lines(raw_prompt, fixed)) < len(lost) and len(fixed) <= PROMPT_LIMIT:
            out, lost = fixed, missing_lines(raw_prompt, fixed)
    return {"prompt": out, "chars": len(out), "missing_lines": lost}


def instruction_language(raw: str) -> str:
    """The language the dubbing instruction (the prompt's first paragraph) is written in."""
    head = raw.strip().split("\n\n")[0]
    cjk = len(re.findall(r"[\u4e00-\u9fff]", head))
    return "Chinese" if cjk > len(head) * 0.2 else "English"


def missing_lines(raw: str, optimized: str) -> list:
    """Bold-quoted lines from the shot prompts that the optimised prompt no longer contains verbatim."""
    norm = lambda t: re.sub(r"[\s“”\"‘’']+", " ", t).strip().lower()
    have = norm(optimized)
    lines = []
    for m in re.finditer(r"\*\*(?:[A-Z][A-Z .'’-]*:\s*)?[\"“]([^\"”*]+)[\"”]\*\*", raw):
        line = m.group(1).strip()
        if line and norm(line) not in have and line not in lines:
            lines.append(line)
    return lines


# ---- building the redub prompt ------------------------------------------------------------------

_CINEMATOGRAPHY = re.compile(
    r"(?:\*\*)?CINEMATOGRAPHY:(?:\*\*)?.*?(?=(?:\*\*)?AUDIO:|\Z)|【Cinematography】.*?(?=【|\Z)", re.S | re.I)
_RANGE = re.compile(r"(\d+(?:\.\d+)?)\s*[–-]\s*(\d+(?:\.\d+)?)\s*s\b")
_RUNTIME = re.compile(r"\s*\d+(?:\.\d+)?\s*-?\s*second total runtime\.?", re.I)
_QUOTED = re.compile(r"(\"[^\"]*\"|“[^”]*”)")


def strip_cinematography(text: str) -> str:
    """Drops CINEMATOGRAPHY blocks (lens, grade, camera moves): nothing in them is heard."""
    return re.sub(r"\n{3,}", "\n\n", _CINEMATOGRAPHY.sub("", text or "")).strip()


def retime(text: str, offset: float) -> str:
    """A shot's local beat times ("2–7s") -> times in the video being dubbed ("32.0–37.0s"), so several
    shots read as one timeline. Quoted dialogue is never touched."""
    def shift(m):
        return f"{float(m.group(1)) + offset:.1f}–{float(m.group(2)) + offset:.1f}s"
    parts = _QUOTED.split(text)
    return "".join(p if i % 2 else _RANGE.sub(shift, _RUNTIME.sub("", p)) for i, p in enumerate(parts))


def build_prompt(header: str, shots: list) -> str:
    """header: pre-prompt + voice lines; shots: [{"label", "prompt", "start", "end"}] in video time."""
    blocks = [f"Shot {s['label']} ({float(s['start']):.1f}s–{float(s['end']):.1f}s):\n"
              + retime(strip_cinematography(s["prompt"]), float(s["start"])) for s in shots if str(s.get("prompt") or "").strip()]
    return header.strip() + ("\n\n" + "\n\n".join(blocks) if blocks else "")


def run_part(payload: dict, progress) -> dict:
    """One dub part: combine its shots -> build its prompt (video time starts at 0) -> optimize with the
    Seed Audio 1.5 skill when asked or when over the limit -> redub. stop_after="prompt" ends after the
    prompt; prompt_override redubs with the user's own prompt."""
    from pipeline import steps

    shots = [s for s in payload.get("shots") or [] if isinstance(s, dict)]
    if not shots or not all(s.get("video") for s in shots):
        raise ValueError("Every shot in this part needs a finished video.")
    voices = [v for v in payload.get("voices") or [] if isinstance(v, dict) and v.get("ref")]
    if len(voices) > 6:
        raise ValueError("Seed Audio takes at most 6 reference voices.")

    combined = payload.get("combined") if isinstance(payload.get("combined"), dict) else None
    if not (combined and combined.get("url") and steps.local_path(combined["url"]) and len(combined.get("spans") or []) == len(shots)):
        progress("Combining shots")
        combined = steps.combine([s["video"] for s in shots])
    if combined["duration"] >= MAX_VIDEO_SECONDS:
        raise ValueError(f"This part is {combined['duration']:.0f}s — Seed Audio takes videos shorter than 6 minutes. Split it.")
    progress("Building prompt", combined=combined)

    override = str(payload.get("prompt_override") or "").strip()
    if override:
        prompt, info = override, {"optimized": False, "missing_lines": []}
    else:
        timed = [{"label": s.get("label") or str(i + 1), "prompt": s.get("prompt") or "",
                  "start": sp["start"], "end": sp["end"]} for i, (s, sp) in enumerate(zip(shots, combined["spans"]))]
        raw = build_prompt(str(payload.get("header") or ""), timed)
        mode = str(payload.get("optimize") or "auto")
        prompt, info = raw, {"optimized": False, "missing_lines": []}
        if mode == "always" or (mode == "auto" and len(raw) > PROMPT_LIMIT):
            r = optimize(raw, timed, voices, combined["duration"], progress)
            prompt, info = r["prompt"], {"optimized": True, "missing_lines": r["missing_lines"]}
        info["raw_chars"] = len(raw)
    progress("Prompt ready", prompt=prompt, **info)
    if payload.get("stop_after") == "prompt":
        return {"combined": combined, "prompt": prompt, **info}

    output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
    result = steps.redub(combined["url"], prompt, [v["ref"] for v in voices], output, progress)
    return {"combined": combined, "prompt": prompt, **info, "result": result}
