"""Seed Audio 1.5 (early access) — speech, voice cloning, dubbing, translation and stem separation.

Ported from the standalone Seed Audio Studio (Documents/audio1.5). Requests are
synchronous on the ModelArk side and can take minutes. Reference clips live in
OUTPUT_DIR and are sent to ModelArk as data URIs; generated audio is copied into
OUTPUT_DIR because the ModelArk links expire after 24 hours, so a project keeps
playing its results.
"""

import base64
import json
import mimetypes
import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from typing import Optional, Tuple

from dotenv import load_dotenv

from content_creator.config.settings import OUTPUT_DIR, PROJECT_ROOT, get_env

load_dotenv(dotenv_path=os.path.join(PROJECT_ROOT, ".env"))

DEFAULT_MODEL = "ep-20260921143824-2m8c9"
DEFAULT_BASE_URL = "https://ark.ap-southeast.bytepluses.com"
GENERATION_TIMEOUT_S = 15 * 60
DOWNLOAD_TIMEOUT_S = 120
REFERENCE_EXTS = (".wav", ".mp3")


def _api_key() -> str:
    # Seed Audio is early access and usually has its own key; fall back to the app's key.
    return get_env("SEED_AUDIO_API_KEY") or get_env("ARK_API_KEY") or ""


def model_id() -> str:
    return get_env("SEED_AUDIO_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL


def _base_url() -> str:
    return (get_env("SEED_AUDIO_BASE_URL", DEFAULT_BASE_URL) or DEFAULT_BASE_URL).rstrip("/")


def max_concurrency() -> int:
    try:
        return max(1, int(get_env("SEED_AUDIO_MAX_CONCURRENCY", "2") or "2"))
    except ValueError:
        return 2


# Early access allows 2 concurrent requests per key.
_slots = threading.BoundedSemaphore(max_concurrency())


def config() -> dict:
    return {"model": model_id(), "has_server_key": bool(_api_key()), "max_concurrency": max_concurrency()}


def save_reference(data_url: str, filename: str) -> str:
    """Stores an uploaded/recorded WAV or MP3 clip and returns its /files/ URL."""
    header, _, b64 = data_url.partition(",")
    if not header.startswith("data:") or not b64:
        raise ValueError("audio_data_url must be a base64 data URL")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in REFERENCE_EXTS:
        ext = ".mp3" if "mpeg" in header or "mp3" in header else ".wav"
    stem = os.path.splitext(os.path.basename(filename))[0]
    stem = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in stem).strip("-")[:60] or "clip"
    name = f"seed-audio-ref-{int(time.time() * 1000)}-{stem}{ext}"
    with open(os.path.join(OUTPUT_DIR, name), "wb") as f:
        f.write(base64.b64decode(b64))
    return "/files/" + name


def _local_file_to_data_uri(url: str) -> str:
    name = os.path.basename(url[len("/files/"):])
    path = os.path.join(OUTPUT_DIR, name)
    if not name or not os.path.isfile(path):
        raise ValueError(f"Reference clip not found on the server: {name or url}")
    # The API wants a lowercase format: data:audio/wav;base64,... or data:audio/mp3;base64,...
    fmt = "mp3" if name.lower().endswith(".mp3") else "wav"
    with open(path, "rb") as f:
        return f"data:audio/{fmt};base64," + base64.b64encode(f.read()).decode("ascii")


def _resolve_local_references(body: dict) -> None:
    for item in body.get("content") or []:
        if not isinstance(item, dict) or item.get("type") != "audio_url":
            continue
        ref = item.get("audio_url") or {}
        url = str(ref.get("url") or "")
        if url.startswith("/files/"):
            ref["url"] = _local_file_to_data_uri(url)


def _keep_copy(url: str, fmt: str, index: int) -> str:
    """Downloads a generated file into OUTPUT_DIR; returns its /files/ URL, or "" on failure."""
    ext = {"ogg_opus": "ogg", "pcm": "pcm", "mp3": "mp3"}.get(fmt, "wav")
    try:
        with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_S) as resp:
            raw = resp.read()
            guessed = mimetypes.guess_extension((resp.headers.get("Content-Type") or "").split(";")[0].strip())
        if guessed in (".mp3", ".wav", ".ogg") and fmt not in ("pcm",):
            ext = guessed.lstrip(".")
        name = f"seed-audio-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}-{index + 1}.{ext}"
        with open(os.path.join(OUTPUT_DIR, name), "wb") as f:
            f.write(raw)
        return "/files/" + name
    except Exception:
        return ""


class BusyError(Exception):
    pass


def generate(body: dict, api_key_override: Optional[str] = None, wait_s: float = 0) -> Tuple[int, dict]:
    """Forwards a Seed Audio request to ModelArk. Returns (http_status, response_json).

    Each returned audio gets a `local_url` (a /files/ copy) next to its expiring `url`.
    """
    key = (api_key_override or "").strip() or _api_key()
    if not key:
        return 401, {"error": "No Seed Audio API key. Set SEED_AUDIO_API_KEY (or ARK_API_KEY) in .env and restart."}

    body = dict(body)
    body["model"] = str(body.get("model") or "").strip() or model_id()
    _resolve_local_references(body)

    # wait_s > 0: queue for a slot (background jobs) instead of failing at once.
    acquired = _slots.acquire(timeout=wait_s) if wait_s > 0 else _slots.acquire(blocking=False)
    if not acquired:
        raise BusyError(f"Max {max_concurrency()} concurrent Seed Audio generations. Wait for one to finish.")
    try:
        req = urllib.request.Request(
            f"{_base_url()}/api/v3/contents/generations",
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=GENERATION_TIMEOUT_S) as resp:
                status, raw = resp.status, resp.read()
        except urllib.error.HTTPError as e:
            status, raw = e.code, e.read()
    finally:
        _slots.release()

    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except ValueError:
        data = {"error": {"message": raw.decode("utf-8", "replace") or f"HTTP {status}"}}

    if not (200 <= status < 300):
        _log_error(status, data, body)
    if 200 <= status < 300:
        fmt = str(data.get("output_format") or body.get("output_format") or "wav")
        for i, audio in enumerate(((data.get("content") or {}).get("audios")) or []):
            if isinstance(audio, dict) and audio.get("url"):
                audio["local_url"] = _keep_copy(audio["url"], fmt, i)
    return status, data


def _log_error(status: int, data: dict, body: dict) -> None:
    """Every failed Seed Audio call, with its full message, request id and what was sent."""
    from content_creator.utils import errorlog

    err = data.get("error") if isinstance(data.get("error"), dict) else {"message": str(data.get("error") or data)}
    roles = [c.get("role") or c.get("type") for c in body.get("content") or [] if isinstance(c, dict)]
    errorlog.log("seed-audio", str(err.get("message") or ""), http_status=status, code=err.get("code") or "",
                 model=body.get("model") or "", inputs=roles,
                 prompt_chars=sum(len(c.get("text") or "") for c in body.get("content") or [] if isinstance(c, dict)),
                 target_language=(body.get("dubbing_config") or {}).get("target_language"))


def _fetch_to(url: str, path: str) -> None:
    """Copies a /files/ URL or an http(s) URL to a local path."""
    if url.startswith("/files/"):
        src = os.path.join(OUTPUT_DIR, os.path.basename(url[len("/files/"):]))
        if not os.path.isfile(src):
            raise ValueError(f"File not found on the server: {url}")
        with open(src, "rb") as r, open(path, "wb") as w:
            w.write(r.read())
        return
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"Unsupported URL: {url[:80]}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT_S) as resp, open(path, "wb") as w:
        w.write(resp.read())


def replace_video_audio(video_url: str, audio_url: str) -> str:
    """Dialogue Fix step 3: drops the video's own audio and attaches the Seed Audio track.

    The video stream is copied untouched; the new audio is padded with silence or cut
    so the result is exactly as long as the original video. Returns a /files/ URL.
    """
    import subprocess
    import tempfile

    from content_creator.services.long_duration import ffmpeg_missing_message, get_ffmpeg_path

    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError(ffmpeg_missing_message())
    with tempfile.TemporaryDirectory(prefix="seed-audio-mux-") as tmp:
        video_path = os.path.join(tmp, "video.mp4")
        audio_path = os.path.join(tmp, "audio" + (os.path.splitext(audio_url.split("?")[0])[1] or ".wav"))
        _fetch_to(video_url, video_path)
        _fetch_to(audio_url, audio_path)
        name = f"seed-audio-final-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}.mp4"
        out_path = os.path.join(OUTPUT_DIR, name)
        base = [ffmpeg, "-y", "-i", video_path, "-i", audio_path, "-map", "0:v:0", "-map", "1:a:0"]
        tail = ["-c:a", "aac", "-b:a", "192k", "-af", "apad", "-shortest", "-movflags", "+faststart", out_path]
        p = subprocess.run(base + ["-c:v", "copy"] + tail, capture_output=True, text=True)
        if p.returncode != 0 or not os.path.isfile(out_path):
            p = subprocess.run(base + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18"] + tail, capture_output=True, text=True)
        if p.returncode != 0 or not os.path.isfile(out_path):
            raise RuntimeError(f"ffmpeg could not replace the audio: {(p.stderr or '').strip()[-2000:]}")
    return "/files/" + name


def upload_source_video(data_url: str) -> str:
    """Uploads a dubbing/translation source video to TOS and returns its public URL.

    Seed Audio only reads videos from public URLs, so local files go through the
    same TOS bucket the Seedance tools use (converted to MP4 and resized into the
    480p-1080p range on the way).
    """
    from content_creator.services.assets import load_asset_env, upload_data_url_to_tos
    from content_creator.services.long_duration import ensure_video_data_url_mp4

    if not data_url.startswith("data:video/"):
        raise ValueError("Upload a video file (MP4 or MOV).")
    return upload_data_url_to_tos(
        data_url=ensure_video_data_url_mp4(video_data_url=data_url),
        env=load_asset_env(),
        key_hint="seed-audio-source",
    )
