"""Audiobook Creator: document -> Seed LLM-annotated segments -> Seed Audio 1.5 -> one audiobook file.

Pipeline
1. extract_text: PDF / DOCX / EPUB / TXT / MD.
2. split_segments: chapters come from headings; inside a chapter Seed LLM picks the
   cut points (scene changes, finished conversations, topic shifts) for segments of up
   to ~300 s of speech — Seed Audio 1.5 voices up to 360 s per call. It returns only
   paragraph ranges, so the book's wording is owned by code, never by the LLM.
3. Seed LLM casts the book (title, language, narrator and character voices, in the
   Seed Audio prompt-guide vocabulary). Code cuts each segment into spans (heading,
   narration paragraphs, quoted dialogue) and Seed LLM only labels each span with a
   speaker and a delivery, so the text sent to Seed Audio is always exactly the book's.
4. build_prompt assembles each Seed Audio prompt in the guide's format: speakers
   defined first, then `Name（@AudioN, delivery）："line"`, a do-not-read-labels rule,
   recording constraints and an optional BGM bed kept under the voice.
5. Voice lock: short voice samples per cast member are passed as reference audio
   (@Audio1, @Audio2 …) so every segment keeps the same voices.
6. assemble: ffmpeg joins the segments with paragraph/chapter pauses, normalises
   loudness and writes an M4B (chapter markers) and an MP3.

Analysis and rendering run as background jobs, polled by job id.
"""

import base64
import io
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from typing import Optional

from content_creator.config.settings import OUTPUT_DIR
from content_creator.services import seed_audio

MAX_DOC_BYTES = 40 * 1024 * 1024
MAX_CHARS = 600_000            # ~100k words; longer books should be split
WORDS_PER_MINUTE = 150
# Segment sizing is by estimated speech time. Measured limits of Seed Audio 1.5:
# plain text-to-speech returns up to 360 s per call, but a take that uses reference
# voices (@Audio1…, i.e. locked voices) is cut off at exactly 240 s. The caps below
# leave ~17-20% headroom for slow narrators, pauses and the "slightly slower" pace.
SEED_AUDIO_LIMIT_S = {"tts": 360, "reference": 240}
SEGMENT_CAP_S = {"tts": 340, "reference": 225}       # estimated seconds; ~6% under the measured limits
LENGTHS = {"scene": 120, "long": 170, "max": None}     # target seconds; max = fill each take to the cap
CUE_SECONDS = 2.0                                       # time a sound cue adds to a segment
MAX_SEGMENT_S = SEGMENT_CAP_S["tts"]                   # module default; split_segments takes the mode
WORDS_PER_SECOND = 2.3                                  # ~138 wpm audiobook pace
CJK_CHARS_PER_SECOND = 4.5
ANNOTATE_WORDS_PER_CALL = 3000
SEGMENT_WINDOW_S = 1500                                 # text Seed LLM segments per call (~25 min)
MAX_SPEAKERS_PER_SEGMENT = 6   # Seed Audio accepts up to 6 reference clips


# ============================================================ text extraction

def _decode_data_url(data_url: str) -> bytes:
    head, _, b64 = data_url.partition(",")
    if not head.startswith("data:") or not b64:
        raise ValueError("document must be a base64 data URL")
    raw = base64.b64decode(b64)
    if len(raw) > MAX_DOC_BYTES:
        raise ValueError("Document is larger than 40 MB.")
    return raw


def _strip_markup(xml: str) -> str:
    xml = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", xml)
    xml = re.sub(r"(?i)<br\s*/?>", "\n", xml)
    xml = re.sub(r"(?i)</(p|div|h[1-6]|li|tr|blockquote|section)>", "\n\n", xml)
    xml = re.sub(r"<[^>]+>", " ", xml)
    return unescape(xml)


_DOCX_TOKENS = re.compile(
    r'<w:t(?:\s[^>]*)?>([^<]*)</w:t>'          # text run
    r'|<w:(?:br|cr)\b[^>]*/>'                  # line break inside a paragraph
    r'|<w:tab\b[^>]*/>'                        # tab
    r'|<w:noBreakHyphen/>'
    r'|</w:p>'                                 # end of paragraph
    r'|<w:pStyle\s+w:val="([^"]+)"')


def _docx_part_text(xml: str) -> list:
    """Paragraphs of one WordprocessingML part, walked token by token in document
    order so nothing is skipped: text in tables and text boxes, line breaks and
    tabs (as spaces — never gluing two words together). Deleted tracked changes
    (w:delText) and field codes are not document text and are left out."""
    paras, buf, style = [], [], ""
    for m in _DOCX_TOKENS.finditer(xml):
        tok = m.group(0)
        if m.group(1) is not None:
            buf.append(unescape(m.group(1)))
        elif m.group(2) is not None:
            style = m.group(2)
        elif tok == "</w:p>":
            text = re.sub(r"[ \t]+", " ", "".join(buf)).strip()
            if text:
                if style and re.match(r"(?i)(heading|title)", style):
                    text = "# " + text
                paras.append(text)
            buf, style = [], ""
        elif tok == "<w:noBreakHyphen/>":
            buf.append("-")
        else:
            buf.append(" ")
    tail = re.sub(r"[ \t]+", " ", "".join(buf)).strip()
    if tail:
        paras.append(tail)
    return paras


def _docx_text(raw: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names = set(z.namelist())
        paras = _docx_part_text(z.read("word/document.xml").decode("utf-8", "replace"))
        # Footnotes and endnotes are part of the document's words too; they are
        # read after the main text.
        for part in ("word/footnotes.xml", "word/endnotes.xml"):
            if part in names:
                notes = [p for p in _docx_part_text(z.read(part).decode("utf-8", "replace")) if p.strip()]
                if notes:
                    paras.append("# Notes" if part.endswith("footnotes.xml") else "# Endnotes")
                    paras.extend(notes)
    return "\n\n".join(paras)


def _epub_text(raw: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names = z.namelist()
        opf = next((n for n in names if n.endswith(".opf")), None)
        order = []
        if opf:
            opf_xml = z.read(opf).decode("utf-8", "replace")
            base = os.path.dirname(opf)
            manifest = dict(re.findall(r'<item[^>]*id="([^"]+)"[^>]*href="([^"]+)"', opf_xml))
            manifest.update({k: v for v, k in re.findall(r'<item[^>]*href="([^"]+)"[^>]*id="([^"]+)"', opf_xml)})
            for idref in re.findall(r'<itemref[^>]*idref="([^"]+)"', opf_xml):
                href = manifest.get(idref)
                if href:
                    order.append(os.path.normpath(os.path.join(base, href)).replace("\\", "/"))
        if not order:
            order = sorted(n for n in names if n.endswith((".xhtml", ".html", ".htm")))
        parts = []
        for n in order:
            if n in names:
                html = z.read(n).decode("utf-8", "replace")
                html = re.sub(r"(?is)<h[1-3][^>]*>(.*?)</h[1-3]>", lambda m: "\n\n# " + re.sub(r"<[^>]+>", " ", m.group(1)).strip() + "\n\n", html)
                parts.append(_strip_markup(html))
        return "\n\n".join(parts)


def extract_text(filename: str, data_url: str) -> str:
    raw = _decode_data_url(data_url)
    ext = os.path.splitext(filename.lower())[1]
    if ext == ".pdf":
        from content_creator.services.script_to_prompt import extract_text_from_pdf_bytes
        text = extract_text_from_pdf_bytes(raw)
    elif ext == ".docx":
        text = _docx_text(raw)
    elif ext == ".epub":
        text = _epub_text(raw)
    elif ext in (".txt", ".md", ".markdown", ".text", ""):
        text = raw.decode("utf-8", "replace")
    elif ext in (".html", ".htm"):
        text = _strip_markup(raw.decode("utf-8", "replace"))
    else:
        raise ValueError("Upload a PDF, DOCX, EPUB, TXT or Markdown file.")
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        raise ValueError("No readable text found (scanned PDFs need OCR first).")
    if len(text) > MAX_CHARS:
        raise ValueError(f"The document has {len(text):,} characters; split it into parts under {MAX_CHARS:,}.")
    return text


# ============================================================== segmentation

_HEADING = re.compile(r"^(#{1,3}\s+.+|(chapter|part|book|prologue|epilogue|preface|introduction|afterword|act|scene|ep|episode)\b\.?\s*[^\n]{0,80})$", re.I)
# Production cues in audio-drama scripts (PocketFM style): "SFX: FOOTSTEPS",
# "AMBIENT SFX: COUNTY CLERK'S OFFICE", "MUSIC: MYSTERIOUS STRINGS", "[BGM: …]".
# They are performed as sound design, never read aloud.
_CUE = re.compile(r"^[\[(]?\s*((?:ambient\s+)?(?:sfx|sound(?:\s+effects?)?|fx|music|bgm|score|ambien(?:ce|t)))\s*[:\-–—]\s*(.+?)\s*[\])]?\s*$", re.I)


def cue_of(para: str):
    """("sfx" | "ambience" | "bgm", description) for a sound-cue line, else None."""
    p = para.strip()
    if len(p) > 140 or "\n" in p:
        return None
    m = _CUE.match(p)
    if not m:
        return None
    label = m.group(1).lower()
    kind = "bgm" if re.search(r"music|bgm|score", label) else "ambience" if "ambien" in label else "sfx"
    return kind, m.group(2).strip().rstrip(".").capitalize() if m.group(2).isupper() else m.group(2).strip().rstrip(".")


# Document metadata ("Word Count: 1388", "Page 3 of 12"): kept with the segment and
# counted by the word check, but not spoken unless the user asks for it.
_METADATA = re.compile(r"^\s*(word\s*count|character\s*count|char\s*count|page\s+\d+(\s+of\s+\d+)?)\s*[:\-–—]?\s*[\d,.\s]*$", re.I)
_METADATA_PREFIX = re.compile(r"^\s*(word|character|char)\s*count\s*[:\-–—]?\s*[\d,.]+\s*", re.I)


def is_metadata(para: str) -> bool:
    p = para.strip()
    return len(p) <= 60 and bool(_METADATA.match(p))


def _para_seconds(para: str) -> float:
    if is_metadata(para):
        return 0.0
    return CUE_SECONDS if cue_of(para) else est_seconds(para)


def _is_heading(para: str) -> bool:
    p = para.strip()
    if len(p) > 90 or "\n" in p or cue_of(p):
        return False
    if _HEADING.match(p):
        return True
    letters = re.sub(r"[^A-Za-z]", "", p)
    return len(letters) >= 4 and letters.isupper() and len(p.split()) <= 8


def _sentences(text: str):
    """Sentences of a paragraph. Splits only on the whitespace after a sentence end
    (optionally followed by a closing quote or bracket), so no character is lost."""
    return [s for s in re.split(r"(?<=[.!?…。！？])\s+|(?<=[.!?…。！？][\"'”’)])\s+", text) if s and s.strip()]


def est_seconds(text: str) -> float:
    """Estimated narration time. CJK scripts have no spaces, so count characters."""
    cjk = len(re.findall(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", text))
    if cjk > len(text) * 0.3:
        return cjk / CJK_CHARS_PER_SECOND
    return len(text.split()) / WORDS_PER_SECOND


def _chapters(text: str) -> list:
    """[{title, paras}] — paragraphs grouped under their heading. A paragraph too long
    for one segment is split at sentence boundaries."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chapters = [{"title": "", "heading": "", "paras": []}]
    for para in paras:
        if _is_heading(para):
            chapters.append({"title": para.lstrip("# ").strip(), "heading": para.lstrip("# ").strip(), "paras": []})
            continue
        if est_seconds(para) > _cap() * 0.8:
            buf = []
            for sent in _sentences(para):
                if buf and est_seconds(" ".join(buf + [sent])) > _cap() * 0.6:
                    chapters[-1]["paras"].append(" ".join(buf))
                    buf = []
                buf.append(sent)
            if buf:
                chapters[-1]["paras"].append(" ".join(buf))
        else:
            chapters[-1]["paras"].append(para)
    return [c for c in chapters if c["paras"] or c["heading"]]


def count_headings(text: str) -> int:
    return sum(1 for c in _chapters(text) if c["heading"])


def count_cues(text: str) -> int:
    return sum(1 for p in re.split(r"\n\s*\n", text) if cue_of(p))


def _pack(paras: list, target_s: float) -> list:
    """Greedy fallback: consecutive paragraphs up to the target length. Returns [[start, end]]."""
    groups, start, acc = [], 0, 0.0
    for i, p in enumerate(paras):
        t = _para_seconds(p)
        if i > start and (acc + t > target_s or acc + t > _cap()):
            groups.append([start, i - 1])
            start, acc = i, 0.0
        acc += t
    if paras:
        groups.append([start, len(paras) - 1])
    return groups


def _fill(paras: list, cap: float, prefer_scene_breaks: bool = True) -> list:
    """Fewest-calls segmentation: pack paragraphs until the next one would pass the
    cap, then start a new segment. When it costs no extra segment, a cut inside the
    last ~10% moves to just before a sound cue (a scene change)."""
    def greedy(prefer):
        groups, start, acc = [], 0, 0.0
        for i, para in enumerate(paras):
            t = _para_seconds(para)
            if i > start and acc + t > cap:
                cut = i
                if prefer:
                    run = acc
                    for j in range(i - 1, start, -1):
                        run -= _para_seconds(paras[j])
                        if run < cap * 0.9:
                            break
                        if cue_of(paras[j]):
                            cut = j
                            break
                groups.append([start, cut - 1])
                start = cut
                acc = sum(_para_seconds(p) for p in paras[cut:i])
            acc += t
        if paras:
            groups.append([start, len(paras) - 1])
        return groups

    plain = greedy(False)
    if not prefer_scene_breaks:
        return plain
    nicer = greedy(True)
    return nicer if len(nicer) <= len(plain) else plain


SEGMENT_SYSTEM = """You split part of an audiobook or audio-drama script into segments for Seed Audio 1.5, which voices one segment per call (max {max_s} seconds).
You get numbered paragraphs with their estimated time in seconds (sound-cue lines like "SFX: …" or "MUSIC: …" are performed as sound, not read). Group CONSECUTIVE paragraphs into segments.
Rules:
- Every segment must total at most {max_s} seconds.
- {fill_rule}
- Within that, cut at the most natural break available: a scene change, a time or place jump, the end of a conversation, a new sound cue that opens a scene, or a paragraph that closes a beat. Never cut in the middle of a dialogue exchange if you can avoid it.
- Cover every paragraph exactly once, in order.
Return ONLY JSON: {"segments": [[first_index, last_index], ...]} with inclusive indexes."""

FILL_RULE = ("Use as FEW segments as possible: fill each segment as close to {max_s} seconds as you can, and end a segment "
             "earlier only at a natural break within its last ~15%. Only the final segment may be short.")
TARGET_RULE = "Aim for about {target_s} seconds per segment, but a natural break matters more than hitting the target."


def _llm_groups(paras: list, target_s: float) -> list:
    listing = "\n\n".join(f"[{i}] ({_para_seconds(p):.0f}s) {p}" for i, p in enumerate(paras))
    fill = target_s >= _cap() * 0.9
    system = (SEGMENT_SYSTEM.replace("{fill_rule}", FILL_RULE if fill else TARGET_RULE)
              .replace("{max_s}", str(int(_cap()))).replace("{target_s}", str(int(target_s))))
    data = _llm_json(system, listing)
    groups = data.get("segments") or []
    # Validate: contiguous, complete, in range; repack any group that is too long.
    out, expect = [], 0
    for g in groups:
        a, b = int(g[0]), int(g[1])
        if a != expect or b < a or b >= len(paras):
            raise ValueError("non-contiguous segmentation")
        if sum(_para_seconds(p) for p in paras[a:b + 1]) > _cap():
            out.extend([[a + x, a + y] for x, y in _pack(paras[a:b + 1], target_s)])
        else:
            out.append([a, b])
        expect = b + 1
    if expect != len(paras):
        raise ValueError("segmentation does not cover every paragraph")
    return out


def _window(paras: list) -> list:
    """Split a long chapter into windows of whole paragraphs Seed LLM can segment in one call."""
    windows, start, acc = [], 0, 0.0
    for i, p in enumerate(paras):
        acc += _para_seconds(p)
        if acc >= SEGMENT_WINDOW_S:
            windows.append((start, i))
            start, acc = i + 1, 0.0
    if start < len(paras):
        windows.append((start, len(paras) - 1))
    return windows


_cap_local = threading.local()


def _cap() -> float:
    return getattr(_cap_local, "cap", MAX_SEGMENT_S)


def split_segments(text: str, length: str = "max", use_llm: bool = True, reference_voices: bool = True,
                   pace: str = "normal") -> list:
    """Chapters from headings; Seed LLM picks the cut points inside each chapter
    (paragraph boundaries only, so the wording is never touched). Falls back to
    greedy packing when a call fails or returns an invalid grouping.
    reference_voices: segments must fit the 240 s reference-voice limit (else 360 s)."""
    cap = SEGMENT_CAP_S["reference" if reference_voices else "tts"]
    if pace == "slower":                       # speech_rate -10 stretches every take ~11%
        cap = int(cap * 0.9)
    target_s = min(LENGTHS.get(length) or cap, cap)
    _cap_local.cap = cap
    chapters = _chapters(text)
    tasks = []
    for ci, ch in enumerate(chapters):
        # Filling to the limit works on the whole chapter (a window edge would force an extra cut).
        windows = [(0, len(ch["paras"]) - 1)] if target_s >= cap else (_window(ch["paras"]) or [(0, -1)])
        for (a, b) in windows:
            tasks.append((ci, a, b))

    def group(task):
        _cap_local.cap = cap
        ci, a, b = task
        paras = chapters[ci]["paras"][a:b + 1]
        if not paras:
            return task, []
        if target_s >= _cap():                 # "max": fill every take to the limit
            return task, _fill(paras, _cap())
        if sum(_para_seconds(p) for p in paras) <= target_s * 1.15 or not use_llm:
            groups = [[0, len(paras) - 1]] if sum(_para_seconds(p) for p in paras) <= _cap() else _pack(paras, target_s)
            if not use_llm:
                groups = _pack(paras, target_s)
            return task, groups
        try:
            return task, _llm_groups(paras, target_s)
        except Exception:
            return task, _pack(paras, target_s)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = dict(pool.map(group, tasks))

    segments = []
    for ci, ch in enumerate(chapters):
        first_in_chapter = True
        for task in [t for t in tasks if t[0] == ci]:
            _, a, b = task
            paras = ch["paras"][a:b + 1]
            for x, y in results.get(task, []):
                body = "\n\n".join(paras[x:y + 1])
                heading = ch["heading"] if first_in_chapter and ch["heading"] else ""
                segments.append({"chapter": ch["title"], "chapter_index": ci,
                                 "heading_text": heading,
                                 "text": (heading + "\n\n" + body) if heading else body})
                first_in_chapter = False
        if first_in_chapter and ch["heading"]:          # a heading with no text under it
            segments.append({"chapter": ch["title"], "chapter_index": ci, "heading_text": ch["heading"], "text": ch["heading"]})
    for i, seg in enumerate(segments):
        seg["id"] = f"s{i + 1:04d}"
        seg["index"] = i
        seg["words"] = len(seg["text"].split())
        seg["est_s"] = round(sum(_para_seconds(p) for p in re.split(r"\n\s*\n", seg["text"]) if p.strip()))
    return segments


def _spans(segment: dict) -> list:
    """The segment cut into speakable spans by code: the chapter heading, narration
    paragraphs, and quoted dialogue. Seed LLM only labels them, so the text sent to
    Seed Audio is always exactly the book's."""
    spans = []
    text = segment["text"]
    heading = segment.get("heading_text") or ""
    if heading and text.startswith(heading):
        spans.append({"kind": "heading", "text": heading})
        text = text[len(heading):].lstrip()
    for para in [p for p in re.split(r"\n\s*\n", text) if p.strip()]:
        if is_metadata(para):
            spans.append({"kind": "meta", "text": para.strip()})
            continue
        cue = cue_of(para)
        if cue:
            spans.append({"kind": "cue", "cue": cue[0], "text": cue[1], "raw": para.strip()})
            continue
        pos = 0
        for m in re.finditer(r"“[^”]+”|\"[^\"\n]+\"", para):
            before = para[pos:m.start()].strip()
            if before:
                spans.append({"kind": "narration", "text": before})
            spans.append({"kind": "quote", "text": m.group(0)})
            pos = m.end()
        rest = para[pos:].strip()
        if rest:
            spans.append({"kind": "narration", "text": rest})
    for i, sp in enumerate(spans):
        sp["i"] = i
    return spans


# ================================================================ Seed LLM

def _llm_json(system: str, user: str) -> dict:
    from byteplussdkarkruntime import Ark

    from content_creator.services.characters import _extract_output_text
    from content_creator.services.model_settings import prompt_model

    resp = Ark().responses.create(
        model=prompt_model(),
        input=[{"role": "system", "content": system},
               {"role": "user", "content": [{"type": "input_text", "text": user}]}],
        temperature=0.2,
        thinking={"type": "disabled"},
    )
    out = _extract_output_text(resp).strip()
    m = re.search(r"\{.*\}", out, re.S)
    if not m:
        raise RuntimeError("Seed LLM returned no JSON.")
    return json.loads(m.group(0))


CAST_SYSTEM = """You are the casting director for an AI-narrated audiobook made with Seed Audio 1.5.
Read the excerpt and return ONLY JSON:
{
  "title": "book title (or a short descriptive title)",
  "language": "the language the text is written in, e.g. English, Hindi, Tamil",
  "genre": "e.g. literary fiction, thriller, children's story, non-fiction, self-help",
  "narrator": "voice description for the narrator",
  "characters": [{"name": "exact name as written", "voice": "voice description", "sample": "15-35 words this character says, for a voice sample: their own dialogue from the text (several lines joined if their lines are short), otherwise a line in character"}],
  "music": "a one-line instrumental underscore idea that suits the genre (used only if the user enables music)"
}
Voice descriptions follow the Seed Audio prompt guide: perceived age + vocal gender + register (deep / mid-low / mid-range / mid-high) + texture (warm, smooth, husky, breathy, crisp…) + distinctive traits + accent when relevant + default emotion + pace. Never use vague words like "pleasant". Example: "30s-40s male, grounded baritone, warm textured tone, calm and immersive, slow measured pace, neutral British accent".
List only characters who actually SPEAK dialogue in the excerpt (max 10), most important first. Narration style requested: {style}. {narrator_hint}"""

ANNOTATE_SYSTEM = """You prepare audiobook segments for Seed Audio 1.5. Each segment is already cut into numbered spans:
"heading" (a chapter title), "narration", or "quote" (dialogue in quotation marks). Label every span with who voices it and how.

Cast: {cast}
Narration style: {style}

Rules:
- "meta" spans (word counts, page numbers) are document information that is not spoken; give them speaker "".
- heading and narration spans are always "Narrator". "cue" spans are sound cues (sound effects, ambience, music) — they are performed, not spoken; give them speaker "" and use them only as context for mood.
- A quote span goes to the character who says it (exact cast name; work it out from dialogue tags and context, e.g. "she said" in the next narration span). If unclear or not in the cast, use "Narrator".{single_note}
- delivery: 2-6 words of performance direction in the Seed Audio guide's vocabulary (emotion, pace, emphasis, volume), e.g. "calm, measured", "tense, hushed, quick", "warm, amused", "slowing for emphasis". Headings: "clear, deliberate, title read". Follow the story's emotional arc across spans.
Return ONLY JSON: {"segments": [{"id": "...", "spans": [{"i": 0, "speaker": "Narrator", "delivery": "..."}]}]}"""


def _norm_words(s: str) -> str:
    return re.sub(r"[\W_]+", "", s.lower(), flags=re.UNICODE)


def _validate_lines(seg_text: str, lines: list) -> bool:
    joined = "".join(str(l.get("text") or "") for l in lines)
    a, b = _norm_words(seg_text), _norm_words(joined)
    if not a:
        return False
    return a == b


def cast_book(text: str, style: str, narrator_hint: str = "") -> dict:
    excerpt = text[:24000]
    hint = f'The user wants this narrator voice: "{narrator_hint}".' if narrator_hint.strip() else ""
    data = _llm_json(CAST_SYSTEM.replace("{style}", style).replace("{narrator_hint}", hint), excerpt)
    chars = []
    for c in (data.get("characters") or [])[:10]:
        if isinstance(c, dict) and str(c.get("name") or "").strip():
            chars.append({"name": str(c["name"]).strip(), "voice": str(c.get("voice") or "").strip(),
                          "sample": str(c.get("sample") or "").strip()})
    return {
        "title": str(data.get("title") or "Audiobook").strip(),
        "language": str(data.get("language") or "").strip(),
        "genre": str(data.get("genre") or "").strip(),
        "narrator": narrator_hint.strip() or str(data.get("narrator") or "").strip(),
        "characters": chars if style == "full-cast" else [],
        "music": str(data.get("music") or "").strip(),
    }


def annotate(segments: list, cast: dict, style: str) -> dict:
    """Returns {segment_id: {"lines": [...], "verified": True}}. The text always comes from
    the code-made spans; a failed or partial LLM answer only loses speaker/delivery labels."""
    names = ["Narrator"] + [c["name"] for c in cast.get("characters", [])]
    cast_desc = "; ".join([f'Narrator ({cast.get("narrator", "")})'] + [f'{c["name"]} ({c["voice"]})' for c in cast.get("characters", [])])
    single_note = "" if style == "full-cast" else "\n- SINGLE NARRATOR: every span's speaker is \"Narrator\"; the narrator performs the characters, so give quote spans a delivery that suggests the character (e.g. \"gruff, older man's voice\")."
    system = ANNOTATE_SYSTEM.replace("{cast}", cast_desc).replace("{style}", style).replace("{single_note}", single_note)
    spans = {s["id"]: _spans(s) for s in segments}
    payload = {"segments": [{"id": s["id"], "spans": [{"i": sp["i"], "kind": sp["kind"], "text": sp["text"]} for sp in spans[s["id"]]]} for s in segments]}
    try:
        data = _llm_json(system, json.dumps(payload, ensure_ascii=False))
        got = {str(x.get("id")): {int(sp.get("i", -1)): sp for sp in (x.get("spans") or []) if isinstance(sp, dict)}
               for x in (data.get("segments") or []) if isinstance(x, dict)}
    except Exception:
        got = {}
    out = {}
    for s in segments:
        labels = got.get(s["id"], {})
        lines = []
        for sp in spans[s["id"]]:
            if sp["kind"] == "meta":
                lines.append({"speaker": "", "delivery": "", "text": sp["text"], "kind": "meta"})
                continue
            if sp["kind"] == "cue":
                lines.append({"speaker": "", "delivery": "", "text": sp["text"], "kind": "cue", "cue": sp["cue"], "raw": sp["raw"]})
                continue
            lab = labels.get(sp["i"], {})
            speaker = str(lab.get("speaker") or "Narrator").strip()
            if sp["kind"] != "quote" or speaker not in names or style != "full-cast":
                speaker = "Narrator"
            delivery = str(lab.get("delivery") or ("clear, deliberate, title read" if sp["kind"] == "heading" else "natural, engaged")).strip()[:80]
            # Merge consecutive narration with the same delivery into one line.
            if lines and sp["kind"] == "narration" and lines[-1]["speaker"] == "Narrator" and lines[-1]["delivery"] == delivery and lines[-1]["kind"] == "narration":
                lines[-1]["text"] += " " + sp["text"]
                continue
            lines.append({"speaker": speaker, "delivery": delivery, "text": sp["text"], "kind": sp["kind"]})
        out[s["id"]] = {"lines": lines, "verified": bool(labels)}
    return out


# ================================================================== prompts

_ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def number_words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("-" + _ONES[n % 10] if n % 10 else "")
    if n < 1000:
        return _ONES[n // 100] + " hundred" + (" " + number_words(n % 100) if n % 100 else "")
    return str(n)


_HEADING_ABBR = re.compile(r"^\s*(ep|eps|episode|ch|chap|chapter|pt|part|bk|book|vol|volume)\.?\s*(\d{1,3})\b", re.I)
_HEADING_WORD = {"ep": "Episode", "eps": "Episode", "episode": "Episode", "ch": "Chapter", "chap": "Chapter",
                 "chapter": "Chapter", "pt": "Part", "part": "Part", "bk": "Book", "book": "Book",
                 "vol": "Volume", "volume": "Volume"}


def spoken_heading(text: str) -> str:
    """How a heading is said aloud: "Ep 1 – The Husband" -> "Episode one – The Husband",
    "Ch. 3" -> "Chapter three". Only the spoken form changes; the document text is kept."""
    m = _HEADING_ABBR.match(text)
    if not m:
        return text
    return _HEADING_WORD[m.group(1).lower()] + " " + number_words(int(m.group(2))) + text[m.end():]


def build_prompt(segment: dict, book: dict, voices: dict) -> dict:
    """Seed Audio request content for one segment.

    voices: {speaker_name: "/files/... sample"} — used as @AudioN references when present.
    Returns {"prompt": str, "refs": [url…]}.
    """
    opts = book.get("options") or {}
    cast = book.get("cast") or {}
    voice_desc = {"Narrator": cast.get("narrator", "")}
    for c in cast.get("characters", []):
        voice_desc[c["name"]] = c.get("voice", "")

    # Sound cues are performed as sound ("perform") or read aloud as written ("read");
    # either way every line of the document stays in the script.
    read_cues = opts.get("cues") == "read"
    read_meta = opts.get("metadata") == "read"
    script = []
    for l in segment["lines"]:
        # Document metadata is never spoken unless asked for — including in segments
        # analyzed before it was recognised, where it was saved as a narration line.
        meta = l.get("kind") == "meta" or (l.get("kind") != "cue" and is_metadata(l.get("text") or ""))
        if meta:
            if read_meta:
                script.append(dict(l, kind="narration", speaker="Narrator", delivery="neutral, informational"))
            continue
        if l.get("kind") != "cue" and not read_meta and _METADATA_PREFIX.match(l.get("text") or ""):
            l = dict(l, text=_METADATA_PREFIX.sub("", l["text"], count=1))
            if not l["text"].strip():
                continue
        if l.get("kind") == "heading":
            l = dict(l, text=spoken_heading(l["text"]))
        if read_cues and l.get("kind") == "cue":
            l = dict(l, kind="narration", speaker="Narrator", delivery="neutral, reading a stage direction",
                     text=l.get("raw") or l["text"])
        script.append(l)
    has_cues = any(l.get("kind") == "cue" for l in script)
    has_music_cue = any(l.get("kind") == "cue" and l.get("cue") == "bgm" for l in script)
    speakers = []
    for l in script:
        if l.get("kind") != "cue" and l["speaker"] not in speakers:
            speakers.append(l["speaker"])
    refs, tag = [], {}
    for sp in speakers:
        url = voices.get(sp)
        if url and len(refs) < MAX_SPEAKERS_PER_SEGMENT:
            refs.append(url)
            tag[sp] = f"@Audio{len(refs)}"

    lang = cast.get("language") or ""
    title = cast.get("title") or "the book"
    where = f', {segment["chapter"]}' if segment.get("chapter") else ""
    header = (f'Audiobook narration of "{title}"{where}.'
              + (f" Spoken in {lang}." if lang else "")
              + " Speak only the quoted text, exactly as written, every word."
              + " Do not read speaker names, labels or directions aloud.")
    sound = ("Perform only the sound effects, ambience and music marked as [SFX], [Ambience] and [BGM] in the script,"
             " each at its place in the script and always below the voices; no other sounds, no room reverb,"
             if has_cues else "no sound effects, no room reverb,")
    recording = ("Dry, close-miked studio recording with a very quiet noise floor; " + sound +
                 " no silence at the beginning or end. Natural breaths and pauses between sentences,"
                 " a slightly longer pause between paragraphs, and a clear pause after a chapter title."
                 " Read the whole script in one continuous take, start to finish, without skipping or repeating anything.")
    music = opts.get("music") or "none"
    if has_music_cue:
        bgm = "Music only where a [BGM] cue asks for it: instrumental, kept low beneath the voices, continuing until the next [BGM] cue or the end."
    elif music == "soft":
        idea = cast.get("music") or "warm, minimal instrumental underscore"
        bgm = f"[BGM] {idea}: instrumental only, very soft and far below the voice throughout, never covering the speech, fading out at the end."
    else:
        bgm = "No music."

    pace = {"slower": " Slightly slower than normal pace.", "faster": " Slightly brisker than normal pace."}.get(opts.get("pace"), "")
    lines = ["[General Requirements]", header, recording + pace, bgm, "[Speakers]"]
    for sp in speakers:
        desc = voice_desc.get(sp) or ("narrator voice" if sp == "Narrator" else "")
        lines.append(f"{sp}: {desc}" + (f", the voice of {tag[sp]}" if sp in tag else "") + ".")
    lines.append("[Script]")
    for l in script:
        if l.get("kind") == "cue":
            tag_name = {"sfx": "SFX", "ambience": "Ambience", "bgm": "BGM"}.get(l.get("cue"), "SFX")
            extra = {"ambience": " — continuous and low under the voices until the next [Ambience] cue",
                     "bgm": " — instrumental only, low beneath the voices"}.get(l.get("cue"), "")
            lines.append(f"[{tag_name}] {l['text']}{extra}.")
            continue
        bits = [b for b in [tag.get(l["speaker"]), l.get("delivery")] if b]
        direction = f"（{', '.join(bits)}）" if bits else ""
        text = l["text"].replace("\n\n", " ").replace("\n", " ")
        # The book's own quote marks would double up inside the prompt's “…”.
        if l.get("kind") == "quote" or l["speaker"] != "Narrator" or re.match(r'^["“”\'‘’].*["“”\'‘’,.!?]$', text):
            text = text.strip().strip('"“”').strip()
        lines.append(f"{l['speaker']}{direction}：“{text}”")
    return {"prompt": "\n".join(lines), "refs": refs}


def strict_sample_prompt(desc: str, line: str, language: str) -> str:
    """Fallback when a sample comes back far longer than its text (the model padded or read extra)."""
    lang = f" in {language}" if language else ""
    return f"{desc}. Say exactly this sentence{lang} and nothing else, then stop: “{line}”"


def sample_prompt(name: str, desc: str, line: str, language: str) -> str:
    lang = f" Spoken in {language}." if language else ""
    return ("[Recording Constraints] Generate only this speaker as dry, close-miked speech. No music, sound effects, "
            "room reverb or second speaker, and no silence at the beginning or end."
            f"{lang}\n{desc}. Deliver the following naturally, speak only the quoted text: “{line}”")


# ==================================================================== jobs

_jobs: dict = {}
_lock = threading.Lock()


def _new_job(kind: str, total: int) -> str:
    job_id = uuid.uuid4().hex[:12]
    with _lock:
        _jobs[job_id] = {"id": job_id, "kind": kind, "status": "running", "stage": "Starting", "done": 0,
                         "total": total, "error": "", "result": None, "items": {}, "created": time.time()}
    return job_id


def _set(job_id: str, **patch) -> None:
    with _lock:
        _jobs[job_id].update(patch)


def _set_item(job_id: str, key: str, value: dict) -> None:
    with _lock:
        _jobs[job_id]["items"][key] = value
        _jobs[job_id]["done"] = sum(1 for v in _jobs[job_id]["items"].values() if v.get("status") in ("done", "error", "truncated"))


def get_job(job_id: str) -> Optional[dict]:
    with _lock:
        job = _jobs.get(job_id)
        return json.loads(json.dumps(job)) if job else None


def _word_list(text: str) -> list:
    return [w for w in (re.sub(r"[\W_]+", " ", t, flags=re.UNICODE).lower() for t in text.split()) for w in w.split()]


def coverage(text: str, segments: list) -> dict:
    """Checks that the segments contain every word of the document, in order.
    Each segment's spoken text comes from its lines (cue lines by their original text)."""
    doc = _word_list(text)
    got = []
    for seg in segments:
        for l in seg.get("lines") or []:
            got.extend(_word_list(l.get("raw") or l.get("text") or ""))
    if doc == got:
        return {"ok": True, "doc_words": len(doc), "covered_words": len(got)}
    import difflib
    sm = difflib.SequenceMatcher(a=doc, b=got, autojunk=False)
    missing = []
    for op, a1, a2, b1, b2 in sm.get_opcodes():
        if op in ("delete", "replace"):
            missing.append(" ".join(doc[a1:a2])[:120])
    return {"ok": False, "doc_words": len(doc), "covered_words": len(got), "missing": missing[:10]}


def start_analysis(text: str, options: dict) -> str:
    style = "full-cast" if options.get("style") == "full-cast" else "single-narrator"
    job_id = _new_job("analyze", 0)

    def run():
        try:
            _set(job_id, stage="Casting voices (Seed LLM)")
            with ThreadPoolExecutor(max_workers=2) as pool:
                cast_f = pool.submit(cast_book, text, style, str(options.get("narrator") or ""))
                _set(job_id, stage="Casting voices and choosing segment breaks (Seed LLM)")
                segments = split_segments(text, options.get("length") or "max",
                                          reference_voices=options.get("lockVoices", True) is not False,
                                          pace=options.get("pace") or "normal")
                cast = cast_f.result()
            _set(job_id, stage=f"Labelling speakers in {len(segments)} segments (Seed LLM)", total=len(segments))
            batches, cur, words = [], [], 0
            for seg in segments:
                if cur and words + seg["words"] > ANNOTATE_WORDS_PER_CALL:
                    batches.append(cur)
                    cur, words = [], 0
                cur.append(seg)
                words += seg["words"]
            if cur:
                batches.append(cur)
            results = {}

            def work(batch):
                results.update(annotate(batch, cast, style))
                with _lock:
                    _jobs[job_id]["done"] = len(results)

            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(work, batches))
            for seg in segments:
                seg["lines"] = results[seg["id"]]["lines"]
                seg["verified"] = results[seg["id"]]["verified"]
            _set(job_id, status="done", stage="Done", done=len(segments),
                 result={"cast": cast, "segments": segments, "style": style, "coverage": coverage(text, segments)})
        except Exception as e:
            from content_creator.utils import errorlog
            errorlog.log("job:audiobook-analyze", str(e), exc=e, job_id=job_id)
            _set(job_id, status="error", stage="Failed", error=str(e))

    threading.Thread(target=run, daemon=True).start()
    return job_id


def _seed_audio_once(body: dict) -> dict:
    """One Seed Audio call, queued behind the app's 2-slot limit, retried on upstream busy."""
    last = ""
    for attempt in range(6):
        status, data = seed_audio.generate(body, wait_s=900)
        if 200 <= status < 300:
            audios = ((data.get("content") or {}).get("audios")) or []
            a = next((x for x in audios if x.get("local_url")), None) or (audios[0] if audios else None)
            if not a:
                raise RuntimeError("Seed Audio returned no audio.")
            return {"url": a.get("local_url") or a.get("url"), "duration_ms": (data.get("usage") or {}).get("duration_ms")}
        err = data.get("error")
        msg = err.get("message") if isinstance(err, dict) else str(err or data)
        code = err.get("code") if isinstance(err, dict) else ""
        last = f"{status}: {msg}"
        if status == 429 or "ConcurrentRequests" in str(code) or status >= 500:
            time.sleep(min(60, 8 * (attempt + 1)))
            continue
        break
    raise RuntimeError(last)


REF_MIN_S, REF_MAX_S = 1.9, 28.0   # Seed Audio accepts reference clips of 1.8-30.2 s


def _duration_s(path: str) -> float:
    out = subprocess.run([_ffmpeg(), "-hide_banner", "-i", path], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", out)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0


def fit_reference_clip(url: str) -> str:
    """Returns a /files/ clip Seed Audio will accept as reference audio: leading and
    trailing silence removed and capped at 28 s (a trimmed copy when needed).
    Preset voices (speaker://<voice id>) are not clips and pass through unchanged."""
    if url.startswith("speaker://"):
        return url
    path = _local_path(url)
    dur = _duration_s(path)
    if REF_MIN_S <= dur <= REF_MAX_S:
        return url
    stem = os.path.splitext(os.path.basename(path))[0]
    name = f"{stem}-ref.wav"
    out = os.path.join(OUTPUT_DIR, name)
    if not os.path.isfile(out):
        trim = ("silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.1,"
                "areverse,silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.1,areverse")
        subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-i", path, "-af", trim, "-t", str(REF_MAX_S),
                        "-ac", "1", "-ar", "44100", "-c:a", "pcm_s16le", out], check=True)
    if _duration_s(out) < REF_MIN_S:
        raise ValueError(f"Voice clip {os.path.basename(path)} is shorter than {REF_MIN_S:.0f} s; use a longer sample.")
    return "/files/" + name


def start_render(items: list, audio_opts: dict) -> str:
    """items: [{id, prompt, refs}] — rendered two at a time (the Seed Audio limit)."""
    job_id = _new_job("render", len(items))
    fmt = audio_opts.get("format") or "wav"

    def one(item):
        _set_item(job_id, item["id"], {"status": "running"})
        content = [{"type": "text", "text": item["prompt"]}]
        for url in (item.get("refs") or [])[:MAX_SPEAKERS_PER_SEGMENT]:
            content.append({"type": "audio_url", "audio_url": {"url": url}, "role": "reference_audio"})
        body = {"content": content, "output_format": fmt,
                "audio_config": {"sample_rate": 44100 if fmt != "ogg_opus" else 48000,
                                 "speech_rate": int(audio_opts.get("speech_rate") or 0), "loudness_rate": 0, "pitch_rate": 0}}
        # Optional Seed Audio endpoint chosen for this book; blank uses the server default.
        if str(audio_opts.get("model") or "").strip():
            body["model"] = str(audio_opts["model"]).strip()
        try:
            res = _seed_audio_once(body)
            # A take that ends exactly at Seed Audio's limit was cut off mid-script.
            # Clip references cut a take off at exactly 240 s; text-only takes and preset
            # voices (speaker://) at 360 s. Only a take ending on a limit was cut off.
            clip_refs = any(not str(c.get("audio_url", {}).get("url", "")).startswith("speaker://") for c in content[1:])
            limit = SEED_AUDIO_LIMIT_S["reference" if clip_refs else "tts"]
            dur = res.get("duration_ms") or 0
            if abs(dur - limit * 1000) <= 250:
                _set_item(job_id, item["id"], {"status": "truncated", **res,
                          "error": f"Cut off at Seed Audio's {limit}-second limit — splitting it in two and re-rendering."})
                return
            _set_item(job_id, item["id"], {"status": "done", **res})
        except Exception as e:
            from content_creator.utils import errorlog
            errorlog.log("job:audiobook-render", str(e), exc=e, job_id=job_id, segment=item["id"])
            _set_item(job_id, item["id"], {"status": "error", "error": str(e)})

    def run():
        # Every reference clip must be 1.8-30 s, or ModelArk rejects the whole segment.
        fitted = {}
        for item in items:
            refs = []
            for url in item.get("refs") or []:
                if url not in fitted:
                    try:
                        fitted[url] = fit_reference_clip(url)
                    except Exception as e:
                        fitted[url] = ""
                        _set(job_id, error=f"Voice clip unusable: {e}")
                if fitted[url]:
                    refs.append(fitted[url])
            item["refs"] = refs
        _set(job_id, stage="Generating audio")
        with ThreadPoolExecutor(max_workers=seed_audio.max_concurrency()) as pool:
            list(pool.map(one, items))
        _set(job_id, status="done", stage="Done")

    threading.Thread(target=run, daemon=True).start()
    return job_id


# ================================================================ assembly

def _ffmpeg() -> str:
    from content_creator.services.long_duration import ffmpeg_missing_message, get_ffmpeg_path
    path = get_ffmpeg_path()
    if not path:
        raise RuntimeError(ffmpeg_missing_message())
    return path


def _local_path(url: str) -> str:
    if not url.startswith("/files/"):
        raise ValueError(f"Segment audio must be a saved /files/ URL: {url[:60]}")
    path = os.path.join(OUTPUT_DIR, os.path.basename(url[len("/files/"):]))
    if not os.path.isfile(path):
        raise ValueError(f"Missing segment audio: {os.path.basename(path)}")
    return path


def assemble(parts: list, title: str, pause_ms: int = 600, chapter_pause_ms: int = 1800) -> dict:
    """parts: [{url, chapter}] in book order. Writes .m4b (chapters) and .mp3; returns their /files/ URLs."""
    ff = _ffmpeg()
    if not parts:
        raise ValueError("No segments to assemble.")
    rate = 44100
    stamp = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}"
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "audiobook").lower()).strip("-")[:40] or "audiobook"
    with tempfile.TemporaryDirectory(prefix="audiobook-") as tmp:
        def silence(ms: int) -> str:
            path = os.path.join(tmp, f"sil-{ms}.wav")
            if not os.path.exists(path):
                subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"anullsrc=r={rate}:cl=mono",
                                "-t", f"{ms / 1000:.3f}", "-c:a", "pcm_s16le", path], check=True)
            return path

        listing, chapters, t_ms = [], [], 0
        current = None
        for i, part in enumerate(parts):
            src = _local_path(part["url"])
            norm = os.path.join(tmp, f"p{i:05d}.wav")
            subprocess.run([ff, "-y", "-loglevel", "error", "-i", src, "-ac", "1", "-ar", str(rate), "-c:a", "pcm_s16le", norm], check=True)
            dur = int((os.path.getsize(norm) - 44) / (rate * 2) * 1000)
            chapter = part.get("chapter") or ""
            if chapter != current:
                if i > 0:
                    listing.append(silence(chapter_pause_ms))
                    t_ms += chapter_pause_ms
                chapters.append({"title": chapter or title or "Opening", "start": t_ms})
                current = chapter
            elif i > 0:
                listing.append(silence(pause_ms))
                t_ms += pause_ms
            listing.append(norm)
            t_ms += dur
        list_path = os.path.join(tmp, "list.txt")
        with open(list_path, "w", encoding="utf-8") as f:
            for p in listing:
                f.write(f"file '{p}'\n")
        joined = os.path.join(tmp, "joined.wav")
        subprocess.run([ff, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", joined], check=True)

        meta = os.path.join(tmp, "meta.txt")
        with open(meta, "w", encoding="utf-8") as f:
            f.write(";FFMETADATA1\n")
            f.write(f"title={title}\nartist=Seed Audio 1.5\ngenre=Audiobook\n")
            for i, ch in enumerate(chapters):
                end = chapters[i + 1]["start"] if i + 1 < len(chapters) else t_ms
                safe = ch["title"].replace("=", "-").replace(";", ",").replace("#", "").replace("\n", " ")
                f.write(f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={ch['start']}\nEND={end}\ntitle={safe}\n")

        # Audiobook loudness (~-18 LUFS, ACX-style), mono speech.
        m4b_name = f"audiobook-{slug}-{stamp}.m4b"
        mp3_name = f"audiobook-{slug}-{stamp}.mp3"
        loud = "loudnorm=I=-18:TP=-3:LRA=11"
        subprocess.run([ff, "-y", "-loglevel", "error", "-i", joined, "-i", meta, "-map_metadata", "1", "-map_chapters", "1",
                        "-af", loud, "-c:a", "aac", "-b:a", "96k", "-f", "mp4", os.path.join(OUTPUT_DIR, m4b_name)], check=True)
        subprocess.run([ff, "-y", "-loglevel", "error", "-i", joined, "-af", loud, "-c:a", "libmp3lame", "-b:a", "128k",
                        "-metadata", f"title={title}", os.path.join(OUTPUT_DIR, mp3_name)], check=True)
    return {"m4b_url": "/files/" + m4b_name, "mp3_url": "/files/" + mp3_name, "duration_ms": t_ms,
            "chapters": chapters}


def estimate_minutes(words: int) -> float:
    return round(words / WORDS_PER_MINUTE, 1)
