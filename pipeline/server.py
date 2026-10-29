"""HTTP server for the short-drama pipeline: the page, /files/ outputs, and JSON APIs."""

import mimetypes
from concurrent.futures import ThreadPoolExecutor
import os
import re
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from content_creator.config.settings import OUTPUT_DIR
from content_creator.services import character_sheet_qa, seed_audio
from content_creator.services.long_duration import has_ffmpeg
from content_creator.services.video import VIDEO_MODELS
from content_creator.utils import errorlog
from content_creator.utils.http import json_response, read_json_body
from pipeline import audio_prompt, jobs, steps, store, tools

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"


def _s(p: dict, key: str) -> str:
    return str(p.get(key) or "").strip()


# ---- routes (payload -> response dict) ------------------------------------------

def api_config(p):
    return {
        "video_models": [{"id": k, **v} for k, v in VIDEO_MODELS.items()],
        "ffmpeg": has_ffmpeg(),
        "keys": {
            "ark": bool(os.getenv("ARK_API_KEY")),
            "assets": all(os.getenv(k) for k in ("BYTEPLUS_AK", "BYTEPLUS_SK", "TOS_BUCKET_NAME")),
            "seed_audio": seed_audio.config()["has_server_key"],
        },
        "dub_limits": {"prompt_chars": audio_prompt.PROMPT_LIMIT, "video_seconds": audio_prompt.MAX_VIDEO_SECONDS},
    }


def api_projects_list(p):
    return {"projects": store.list_projects()}


def api_projects_get(p):
    project = store.get_project(_s(p, "id"))
    if not project:
        raise LookupError("Project not found.")
    return {"project": project}


def api_projects_save(p):
    return store.save_project(p.get("project"))


def api_projects_delete(p):
    store.delete_project(_s(p, "id"))
    return {"ok": True}


def api_job_status(p):
    job = jobs.get(_s(p, "job_id"))
    if not job:
        raise LookupError("This job is no longer running (the server restarted). Start it again.")
    return job


def api_upload(p):
    return {"url": steps.save_upload(_s(p, "data_url"), _s(p, "hint") or "upload")}


def api_script(p):
    script, pdf = _s(p, "script_text"), _s(p, "pdf_data_url")
    return {"job_id": jobs.start("script", lambda progress: steps.script_to_prompts(script, pdf, progress), {"project": _s(p, "project")})}


def api_sheet_start(p):
    refs = [steps.data_url_of(str(u)) for u in p.get("images") or [] if str(u).strip()]
    notes = _s(p, "notes")
    if not refs and len(notes) < 3:
        raise ValueError("Write a character description or add a reference image.")
    job_id = character_sheet_qa.start_sheet_job(refs[:character_sheet_qa.MAX_SEEDREAM_REFS], notes, None)
    errorlog.remember(job_id, project=_s(p, "project"), character=_s(p, "name"))
    return {"job_id": job_id}


def api_sheet_inspect(p):
    character_sheet_qa.start_inspect_job(_s(p, "job_id"))
    return {"job_id": _s(p, "job_id")}


def api_sheet_status(p):
    job = character_sheet_qa.get_job(_s(p, "job_id"))
    if not job:
        raise LookupError("This sheet job is no longer running (the server restarted). Generate again.")
    if job.get("result"):
        job["result"].pop("image_data_url", None)  # the page shows the /files/ copy
    return job


def api_location_start(p):
    direction, count = _s(p, "direction"), max(0, min(6, int(p.get("variations") or 0)))
    return {"job_id": jobs.start("location", lambda progress: steps.location_sheet(direction, count, progress),
                                 {"project": _s(p, "project"), "location": _s(p, "name")})}


def api_library_add(p):
    return steps.add_to_library(_s(p, "group_id"), _s(p, "project_name"), _s(p, "image_url"), _s(p, "name"))


def api_shot_start(p):
    return steps.start_shot(p)


def api_shot_automap(p):
    items = [it for it in p.get("items") or [] if isinstance(it, dict) and it.get("id")]
    shots = [s for s in p.get("shots") or [] if isinstance(s, dict) and s.get("id")]
    maps = {}
    # Three shots per Seed LLM call keeps each call quick and focused; the calls run in parallel.
    with ThreadPoolExecutor(max_workers=6) as pool:
        for part in pool.map(lambda i: steps.auto_map_shots(shots[i:i + 3], items), range(0, len(shots), 3)):
            maps.update(part)
    return {"maps": maps}


def api_shot_status(p):
    return steps.shot_status(_s(p, "task_id"), {"project": _s(p, "project"), "shot": _s(p, "shot")})


def api_combine(p):
    return steps.combine([str(u) for u in p.get("video_urls") or []])


def api_voice_upload(p):
    return {"url": seed_audio.save_reference(_s(p, "data_url"), _s(p, "filename") or "voice.wav")}


def api_dub_part(p):
    shots = [str(s.get("label") or "") for s in p.get("shots") or [] if isinstance(s, dict)]
    return {"job_id": jobs.start("dub-part", lambda progress: audio_prompt.run_part(p, progress),
                                 {"project": _s(p, "project"), "part": _s(p, "part"), "shots": ",".join(shots),
                                  "optimize": _s(p, "optimize")})}


def api_dub_build(p):
    """The raw redub prompt for a preview: header + shots (CINEMATOGRAPHY removed, beats in video time)."""
    shots = [s for s in p.get("shots") or [] if isinstance(s, dict)]
    return {"prompt": audio_prompt.build_prompt(str(p.get("header") or ""), shots)}


def api_tools_get(p):
    return {"state": tools.get_state(_s(p, "key"))}


def api_tools_save(p):
    tools.save_state(_s(p, "key"), p.get("state"))
    return {"ok": True}


def api_tools_dub(p):
    return {"job_id": jobs.start("tool-dub", lambda progress: tools.run_dub(p, progress), {"tool": "Video Dubbing", "video": _s(p, "video")})}


def api_tools_translate(p):
    return {"job_id": jobs.start("tool-translate", lambda progress: tools.run_translate(p, progress),
                                 {"tool": "Video Translation", "video": _s(p, "video"), "target_language": _s(p, "target_language")})}


def api_tools_languages(p):
    return {"languages": tools.TRANSLATION_LANGUAGES}


ROUTES = {
    "/api/config": api_config,
    "/api/projects/list": api_projects_list,
    "/api/projects/get": api_projects_get,
    "/api/projects/save": api_projects_save,
    "/api/projects/delete": api_projects_delete,
    "/api/jobs/status": api_job_status,
    "/api/upload": api_upload,
    "/api/script": api_script,
    "/api/sheet/start": api_sheet_start,
    "/api/sheet/inspect": api_sheet_inspect,
    "/api/sheet/status": api_sheet_status,
    "/api/location/start": api_location_start,
    "/api/library/add": api_library_add,
    "/api/shot/start": api_shot_start,
    "/api/shot/automap": api_shot_automap,
    "/api/shot/status": api_shot_status,
    "/api/combine": api_combine,
    "/api/voice/upload": api_voice_upload,
    "/api/tools/get": api_tools_get,
    "/api/tools/save": api_tools_save,
    "/api/tools/dub": api_tools_dub,
    "/api/tools/translate": api_tools_translate,
    "/api/tools/languages": api_tools_languages,
    "/api/audiobook/extract": tools.audiobook_extract,
    "/api/audiobook/analyze": tools.audiobook_analyze,
    "/api/audiobook/status": tools.audiobook_status,
    "/api/audiobook/prompts": tools.audiobook_prompts,
    "/api/audiobook/voice-sample": tools.audiobook_voice_sample,
    "/api/audiobook/render": tools.audiobook_render,
    "/api/audiobook/assemble": tools.audiobook_assemble,
    "/api/system/capabilities": tools.system_capabilities,
    "/api/system/models": tools.system_models,
    "/api/seed-audio/config": tools.seed_audio_config,
    "/api/seed-audio/upload": tools.seed_audio_upload,
    "/api/dub/part": api_dub_part,
    "/api/dub/build": api_dub_build,
}


def _ids(payload: dict) -> dict:
    """What an API error log entry records about the request: its ids and labels, never file data."""
    keep = ("job_id", "task_id", "name", "label", "shot", "part")
    return {k: str(payload.get(k))[:200] for k in keep if payload.get(k)}


class Handler(BaseHTTPRequestHandler):
    def _send_file(self, path: str, method: str) -> None:
        """Serves a file with HTTP Range support, which <video>/<audio> need to seek."""
        size = os.path.getsize(path)
        start, end, status = 0, size - 1, HTTPStatus.OK
        m = re.match(r"bytes=(\d*)-(\d*)$", (self.headers.get("Range") or "").strip())
        if m and (m.group(1) or m.group(2)) and size:
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            if start > end or start >= size:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            status = HTTPStatus.PARTIAL_CONTENT
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if path.endswith((".js", ".jsx")):
            ctype = "application/javascript; charset=utf-8"
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if path.endswith((".html", ".jsx", ".css")):
            self.send_header("Cache-Control", "no-store")
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if method != "GET":
            return
        try:
            with open(path, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(1024 * 1024, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _get(self, method: str) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            target = STATIC_DIR / "index.html"
        elif path.startswith("/static/"):
            target = STATIC_DIR / os.path.basename(path)
        elif path.startswith("/files/"):
            target = Path(OUTPUT_DIR) / os.path.basename(path)
        else:
            target = None
        if not target or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self._send_file(str(target), method)

    def do_GET(self) -> None:
        self._get("GET")

    def do_HEAD(self) -> None:
        self._get("HEAD")

    def do_POST(self) -> None:
        route = ROUTES.get(self.path.split("?", 1)[0])
        if not route:
            json_response(self, HTTPStatus.NOT_FOUND, {"error": "Unknown route"})
            return
        try:
            payload = read_json_body(self)
        except Exception:
            json_response(self, HTTPStatus.BAD_REQUEST, {"error": "Expected a JSON body"})
            return
        try:
            json_response(self, HTTPStatus.OK, route(payload))
        except Exception as e:
            status = HTTPStatus.BAD_REQUEST if isinstance(e, (ValueError, LookupError)) else \
                HTTPStatus.TOO_MANY_REQUESTS if isinstance(e, seed_audio.BusyError) else HTTPStatus.INTERNAL_SERVER_ERROR
            if status == HTTPStatus.INTERNAL_SERVER_ERROR:
                traceback.print_exc()
            errorlog.log(f"api:{self.path.split('?', 1)[0]}", str(e), exc=e, http_status=int(status),
                         project=str(payload.get("project") or ""), **_ids(payload))
            json_response(self, status, {"error": str(e) or e.__class__.__name__})

    def log_message(self, format: str, *args) -> None:
        if "/api/jobs/status" in self.path or "/status" in self.path:
            return
        super().log_message(format, *args)


def run(host: str, port: int) -> None:
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Short Drama Pipeline running at http://{host}:{port}")
    server.serve_forever()
