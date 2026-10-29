"""Standalone tools outside the drama pipeline: Video Dubbing, Video Translation and Audiobook.

Each tool's form and results are saved in outputs/tools.json (one entry per tool) so they survive
reloads. Long work runs as a background job (pipeline/jobs.py), polled by the page.
"""

import json
import os
import threading

from content_creator.config.settings import OUTPUT_DIR
from content_creator.services import audiobook as ab
from content_creator.services import model_settings, seed_audio
from pipeline import audio_prompt, steps

TOOLS_PATH = os.path.join(OUTPUT_DIR, "tools.json")
_lock = threading.Lock()

# Seed Audio 1.5 video translation targets.
TRANSLATION_LANGUAGES = [
    ["en", "English"], ["zh", "Chinese"], ["ja", "Japanese"], ["ko", "Korean"], ["de", "German"],
    ["fr", "French"], ["pt-BR", "Portuguese (Brazil)"], ["pt-PT", "Portuguese (Portugal)"], ["th", "Thai"],
    ["id", "Indonesian"], ["vi", "Vietnamese"], ["ms", "Malay"], ["fil", "Filipino"], ["it", "Italian"],
    ["ru", "Russian"], ["nl", "Dutch"], ["pl", "Polish"], ["tr", "Turkish"], ["sv", "Swedish"],
    ["es-ES", "Spanish (Castilian)"], ["es-MX", "Spanish (Mexico)"], ["ar", "Arabic"], ["fi", "Finnish"],
    ["da", "Danish"], ["no", "Norwegian"], ["cs", "Czech"], ["hu", "Hungarian"], ["el", "Greek"],
    ["ro", "Romanian"], ["hi", "Hindi"],
]


# ---- saved tool state ----------------------------------------------------------------------------

def _load() -> dict:
    try:
        with open(TOOLS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def get_state(key: str):
    with _lock:
        return _load().get(key)


def save_state(key: str, state) -> None:
    if not key:
        raise ValueError("tool key is required")
    with _lock:
        data = _load()
        data[key] = state
        tmp = TOOLS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, TOOLS_PATH)


# ---- video dubbing -------------------------------------------------------------------------------

def run_dub(payload: dict, progress) -> dict:
    """Any video + a dubbing prompt (+ optional reference voices) -> Seed Audio -> the video with the new audio.
    optimize=True first rewrites the prompt with the Seed Audio 1.5 skill."""
    video = str(payload.get("video") or "").strip()
    prompt = str(payload.get("prompt") or "").strip()
    if not video:
        raise ValueError("Add a video first.")
    if not prompt:
        raise ValueError("Write the dubbing prompt — include every line to be spoken; Seed Audio needs the words.")
    voices = [v for v in payload.get("voices") or [] if isinstance(v, dict) and v.get("ref")]
    if len(voices) > 6:
        raise ValueError("Seed Audio takes at most 6 reference voices.")
    path = steps.local_path(video)
    duration = steps.probe_duration(path) if path else 0.0
    info = {"optimized": False, "missing_lines": []}
    if payload.get("optimize"):
        r = audio_prompt.optimize(prompt, [{"label": "1", "start": 0.0, "end": duration}], voices, duration, progress)
        prompt, info = r["prompt"], {"optimized": True, "missing_lines": r["missing_lines"]}
        progress("Prompt ready", prompt=prompt, **info)
    output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
    result = steps.redub(video, prompt, [v["ref"] for v in voices], output, progress)
    return {"prompt": prompt, **info, "result": result}


# ---- video translation ---------------------------------------------------------------------------

def run_translate(payload: dict, progress) -> dict:
    """A video -> Seed Audio video translation (no prompt) -> the video with the translated audio."""
    video = str(payload.get("video") or "").strip()
    target = str(payload.get("target_language") or "").strip()
    if not video:
        raise ValueError("Add a video first.")
    if target not in {code for code, _ in TRANSLATION_LANGUAGES}:
        raise ValueError("Choose a target language.")
    path = steps.local_path(video)
    if path and steps.probe_duration(path) >= audio_prompt.MAX_VIDEO_SECONDS:
        raise ValueError("Seed Audio takes videos shorter than 6 minutes.")
    source = video
    if not source.startswith("http"):
        progress("Uploading video (Seed Audio reads public URLs)")
        source = seed_audio.upload_source_video(steps.data_url_of(video))
    glossaries = [{"source": str(g.get("source")).strip(), "target": str(g.get("target")).strip()}
                  for g in payload.get("glossaries") or [] if isinstance(g, dict) and str(g.get("source") or "").strip() and str(g.get("target") or "").strip()]
    body = {"content": [{"type": "video_url", "video_url": {"url": source}, "role": "dubbing_video"}],
            "dubbing_config": {"target_language": target, **({"glossaries": glossaries} if glossaries else {})}}
    progress("Seed Audio is translating (this can take several minutes)")
    status, data = seed_audio.generate(body, wait_s=15 * 60)
    if not (200 <= status < 300):
        err = data.get("error")
        raise RuntimeError((err.get("message") if isinstance(err, dict) else str(err or "")) or f"Seed Audio HTTP {status}")
    audios = [a for a in ((data.get("content") or {}).get("audios") or []) if isinstance(a, dict)]
    if not audios:
        raise RuntimeError("Seed Audio returned no audio.")
    tracks = [{"type": a.get("type") or "mixed", "url": a.get("local_url") or a.get("url")} for a in audios]
    track = next((t for t in tracks if t["type"] == "mixed"), tracks[0])
    progress("Replacing the video's audio")
    final = seed_audio.replace_video_audio(video, track["url"])
    return {"source_url": source, "audio": track["url"], "tracks": tracks, "final": final}


# ---- audiobook (routes for static/audiobook.jsx) --------------------------------------------------

def audiobook_extract(p: dict) -> dict:
    """{filename, data_url} -> {text, words, est_minutes, headings}."""
    try:
        text = ab.extract_text(str(p.get("filename") or ""), str(p.get("data_url") or ""))
    except (ValueError, RuntimeError):
        raise
    except Exception as e:
        raise RuntimeError(f"Could not read the document: {e}")
    return {"text": text, "words": len(text.split()), "est_minutes": round(ab.est_seconds(text) / 60, 1),
            "headings": ab.count_headings(text)}


def audiobook_analyze(p: dict) -> dict:
    text = str(p.get("text") or "").strip()
    if not text:
        raise ValueError("No document text.")
    return {"job_id": ab.start_analysis(text, p.get("options") or {})}


def audiobook_status(p: dict) -> dict:
    job = ab.get_job(str(p.get("job_id") or ""))
    if not job:
        raise LookupError("Job not found (the server may have restarted).")
    return job


def audiobook_prompts(p: dict) -> dict:
    book, voices, out = p.get("book") or {}, p.get("voices") or {}, {}
    for seg in p.get("segments") or []:
        try:
            out[seg["id"]] = ab.build_prompt(seg, book, voices)
        except Exception as e:
            out[seg.get("id", "?")] = {"error": str(e)}
    return {"prompts": out}


_AUDIO_CONFIG = {"sample_rate": 44100, "speech_rate": 0, "loudness_rate": 0, "pitch_rate": 0}


def audiobook_voice_sample(p: dict) -> dict:
    """{name, voice, line, language} -> a short TTS sample used as the voice reference;
    {preset, line, language} -> a preview of a preset voice (speaker://<id>)."""
    line, voice, preset = (str(p.get(k) or "").strip() for k in ("line", "voice", "preset"))
    model, lang = str(p.get("model") or "").strip(), str(p.get("language") or "")
    if preset:
        if not line:
            raise ValueError("A sample line is required.")
        body = {"content": [{"type": "text", "text": f"Speaker (@Audio1){' speaking ' + lang if lang else ''}: “{line}”"},
                            {"type": "audio_url", "audio_url": {"url": "speaker://" + preset.replace("speaker://", "")}, "role": "reference_audio"}],
                "output_format": "wav", "audio_config": dict(_AUDIO_CONFIG)}
        if model:
            body["model"] = model
        return ab._seed_audio_once(body)
    if not line or not voice:
        raise ValueError("A voice description and a sample line are required.")
    body = {"content": [{"type": "text", "text": ab.sample_prompt(str(p.get("name") or ""), voice, line, lang)}],
            "output_format": "wav", "audio_config": dict(_AUDIO_CONFIG)}
    if model:
        body["model"] = model
    res = ab._seed_audio_once(body)
    # A sample far longer than its text means the model padded it or read extra words: retry once, stricter.
    if ab._duration_s(ab._local_path(res["url"])) > max(12.0, (len(line.split()) / 2.5 + 2) * 2.5):
        body["content"][0]["text"] = ab.strict_sample_prompt(voice, line, lang)
        res = ab._seed_audio_once(body)
    # Samples become reference audio, which must be 1.8-30 s.
    res["url"] = ab.fit_reference_clip(res["url"])
    res["duration_ms"] = int(ab._duration_s(ab._local_path(res["url"])) * 1000)
    return res


def audiobook_render(p: dict) -> dict:
    items = [i for i in (p.get("items") or []) if isinstance(i, dict) and i.get("id") and i.get("prompt")]
    if not items:
        raise ValueError("Nothing to render.")
    return {"job_id": ab.start_render(items, p.get("audio") or {})}


def audiobook_assemble(p: dict) -> dict:
    return ab.assemble(p.get("parts") or [], str(p.get("title") or "Audiobook"),
                       int(p.get("pause_ms") or 600), int(p.get("chapter_pause_ms") or 1800))


# Helpers the audiobook page calls: model choice and Seed Audio config / reference upload.
def system_capabilities(p: dict) -> dict:
    return {"models": model_settings.config()}


def system_models(p: dict) -> dict:
    return model_settings.set_models(str(p.get("prompt") or ""), str(p.get("image") or ""))


def seed_audio_config(p: dict) -> dict:
    return seed_audio.config()


def seed_audio_upload(p: dict) -> dict:
    return {"file_url": seed_audio.save_reference(str(p.get("audio_data_url") or ""), str(p.get("filename") or "clip.wav"))}
