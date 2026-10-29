import base64
import glob
import os
import shutil
import subprocess
import tempfile
import urllib.request
import uuid


def has_ffmpeg() -> bool:
    return bool(_find_ffmpeg())


def get_ffmpeg_path() -> str:
    return _find_ffmpeg()


def ffmpeg_missing_message() -> str:
    return _ffmpeg_missing_message()


def _ffmpeg_missing_message() -> str:
    return "ffmpeg is not installed. Install Homebrew+ffmpeg or install the bundled binary via: pip install imageio-ffmpeg"


def _find_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg
    try:
        import imageio_ffmpeg  # type: ignore

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            return exe
    except Exception:
        pass

    home = os.path.expanduser("~")
    candidates: list[str] = []
    candidates.extend(
        glob.glob(os.path.join(home, "Library", "Python", "*", "lib", "python", "site-packages", "imageio_ffmpeg", "binaries", "ffmpeg-*"))
    )
    candidates.extend(
        glob.glob(os.path.join(home, ".local", "lib", "python*", "site-packages", "imageio_ffmpeg", "binaries", "ffmpeg-*"))
    )
    candidates.extend(
        glob.glob(os.path.join(home, "Library", "Python", "*", "lib", "python*", "site-packages", "imageio_ffmpeg", "binaries", "ffmpeg-*"))
    )
    for exe in candidates:
        if os.path.exists(exe) and os.path.isfile(exe):
            if os.access(exe, os.X_OK):
                return exe
            try:
                st = os.stat(exe)
                os.chmod(exe, st.st_mode | 0o111)
                if os.access(exe, os.X_OK):
                    return exe
            except Exception:
                continue
    return ""


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _download(url: str, out_path: str) -> None:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "*/*",
        },
    )
    with urllib.request.urlopen(req) as resp:
        data = resp.read()
    with open(out_path, "wb") as f:
        f.write(data)


def _split_data_url(data_url: str) -> tuple[str, bytes]:
    v = (data_url or "").strip()
    if not v.startswith("data:") or ";base64," not in v:
        raise RuntimeError("media must be a base64 data URL")
    header, b64 = v.split(",", 1)
    mime = header[5:].split(";", 1)[0].strip().lower() or "application/octet-stream"
    return mime, base64.b64decode(b64)


def _ext_from_mime(mime: str, *, default: str) -> str:
    m = (mime or "").lower()
    if "mp4" in m:
        return ".mp4"
    if "quicktime" in m or "mov" in m:
        return ".mov"
    if "webm" in m:
        return ".webm"
    if "mpeg" in m or "mp3" in m:
        return ".mp3"
    if "wav" in m:
        return ".wav"
    if "m4a" in m or "mp4a" in m or "aac" in m:
        return ".m4a"
    if "ogg" in m:
        return ".ogg"
    return default


def ensure_video_data_url_mp4(*, video_data_url: str) -> str:
    mime, raw = _split_data_url(video_data_url)
    if mime == "video/mp4":
        return video_data_url

    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError(_ffmpeg_missing_message())

    job_id = uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix=f"video-mp4-{job_id}-") as tmp:
        in_path = os.path.join(tmp, "input" + _ext_from_mime(mime, default=".bin"))
        out_path = os.path.join(tmp, "output.mp4")
        with open(in_path, "wb") as f:
            f.write(raw)

        cmd = [
            ffmpeg,
            "-y",
            "-i",
            in_path,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            out_path,
        ]
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode != 0 or not os.path.exists(out_path):
            err = (p.stderr or "").strip()
            raise RuntimeError(f"ffmpeg video transcode failed: {err[:8000]}")

        with open(out_path, "rb") as f:
            mp4 = f.read()
        return "data:video/mp4;base64," + base64.b64encode(mp4).decode("utf-8")


def audio_data_url_to_black_video(*, audio_data_url: str, output_dir: str, image_data_url: str = "") -> str:
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError(_ffmpeg_missing_message())

    mime, raw = _split_data_url(audio_data_url)
    _ensure_dir(output_dir)

    job_id = uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix=f"audio-black-{job_id}-") as tmp:
        in_path = os.path.join(tmp, "input" + _ext_from_mime(mime, default=".bin"))
        image_path = ""
        out_path = os.path.join(output_dir, f"black-audio-{job_id}.mp4")
        with open(in_path, "wb") as f:
            f.write(raw)

        if image_data_url:
            image_mime, image_raw = _split_data_url(image_data_url)
            image_path = os.path.join(tmp, "cover" + _ext_from_mime(image_mime, default=".png"))
            with open(image_path, "wb") as f:
                f.write(image_raw)
            cmd = [
                ffmpeg,
                "-y",
                "-loop",
                "1",
                "-i",
                image_path,
                "-i",
                in_path,
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-vf",
                "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black,format=yuv420p",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-shortest",
                "-movflags",
                "+faststart",
                out_path,
            ]
        else:
            cmd = [
                ffmpeg,
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=1920x1080:r=24",
                "-i",
                in_path,
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-shortest",
                "-movflags",
                "+faststart",
                out_path,
            ]
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode != 0 or not os.path.exists(out_path):
            err = (p.stderr or "").strip()
            raise RuntimeError(f"ffmpeg audio-to-video failed: {err[:8000]}")
        return out_path


def combine_videos_ffmpeg(*, video_urls: list[str], output_dir: str) -> str:
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError(_ffmpeg_missing_message())

    urls = [u.strip() for u in (video_urls or []) if (u or "").strip()]
    if len(urls) < 2:
        raise RuntimeError("Need at least 2 video URLs to combine.")

    _ensure_dir(output_dir)

    job_id = uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix=f"concat-{job_id}-") as tmp:
        input_paths: list[str] = []
        for idx, url in enumerate(urls):
            path = os.path.join(tmp, f"shot-{idx + 1}.mp4")
            _download(url, path)
            if not os.path.exists(path) or os.path.getsize(path) == 0:
                raise RuntimeError(f"Failed to download video: {url}")
            input_paths.append(path)

        list_path = os.path.join(tmp, "inputs.txt")
        with open(list_path, "w", encoding="utf-8") as f:
            for p in input_paths:
                f.write(f"file '{p}'\n")

        out_path = os.path.join(output_dir, f"combined-{job_id}.mp4")

        cmd_copy = [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            list_path,
            "-c",
            "copy",
            out_path,
        ]
        p1 = subprocess.run(cmd_copy, capture_output=True, text=True)
        if p1.returncode == 0 and os.path.exists(out_path):
            return out_path

        cmd_reencode = [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            list_path,
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            out_path,
        ]
        p2 = subprocess.run(cmd_reencode, capture_output=True, text=True)
        if p2.returncode != 0 or not os.path.exists(out_path):
            err = (p2.stderr or p1.stderr or "").strip()
            raise RuntimeError(f"ffmpeg combine failed: {err[:8000]}")
        return out_path


def extract_last_frame_data_url_from_video_data_url(*, video_data_url: str) -> str:
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError(_ffmpeg_missing_message())

    v = (video_data_url or "").strip()
    if not v.startswith("data:") or ";base64," not in v:
        raise RuntimeError("video must be a base64 data URL")

    _, b64 = v.split(",", 1)
    raw = base64.b64decode(b64)

    job_id = uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix=f"last-frame-{job_id}-") as tmp:
        in_path = os.path.join(tmp, "in.mp4")
        out_path = os.path.join(tmp, "last.png")
        with open(in_path, "wb") as f:
            f.write(raw)

        cmd = [
            ffmpeg,
            "-y",
            "-sseof",
            "-0.1",
            "-i",
            in_path,
            "-frames:v",
            "1",
            out_path,
        ]
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode != 0 or not os.path.exists(out_path):
            err = (p.stderr or "").strip()
            raise RuntimeError(f"ffmpeg last frame failed: {err[:8000]}")

        with open(out_path, "rb") as f:
            png = f.read()
        return "data:image/png;base64," + base64.b64encode(png).decode("utf-8")
