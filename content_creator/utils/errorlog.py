"""One complete log of every failure: outputs/errors.log, one JSON line per error.

Each entry has the time, where it happened (source), the full message, any request / task id, the
context it ran in (project, shot, part…), and the Python traceback when there is one. Seed Audio,
Seedance, Seed LLM, Seedream, uploads, background jobs and API errors all write here.
"""

import json
import os
import re
import threading
import time
import traceback as tb

from content_creator.config.settings import OUTPUT_DIR

ERRORS_LOG = os.path.join(OUTPUT_DIR, "errors.log")
_lock = threading.Lock()
_seen: set = set()  # (source, key) pairs already logged, for errors seen again on every poll
_job_context: dict = {}  # job id -> context (project, character…) for jobs started by code that doesn't know it


def remember(job_id: str, **context) -> None:
    """Context to add to any error a job logs later (by its job_id)."""
    if job_id:
        with _lock:
            _job_context[job_id] = {k: v for k, v in context.items() if v}


def request_id(message: str) -> str:
    m = re.search(r"Request id:\s*([0-9a-zA-Z]+)", message or "") or re.search(r"\b(021\d{10,}[0-9a-f]+)\b", message or "")
    return m.group(1) if m else ""


def log(source: str, message: str, *, exc: BaseException = None, once_key: str = "", **context) -> None:
    """source: e.g. "seed-audio", "seedance", "job:dub-part", "api:/api/library/add".
    once_key: log this error only once (e.g. a failed Seedance task id that the page polls repeatedly)."""
    if once_key:
        with _lock:
            if (source, once_key) in _seen:
                return
            _seen.add((source, once_key))
    message = str(message or (exc and (str(exc) or exc.__class__.__name__)) or "")
    entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "source": source, "message": message,
             "request_id": request_id(message)}
    with _lock:
        known = dict(_job_context.get(str(context.get("job_id") or ""), {}))
    entry.update({k: v for k, v in {**known, **context}.items() if v not in (None, "", [], {})})
    if exc is not None:
        entry["error_type"] = exc.__class__.__name__
        entry["traceback"] = "".join(tb.format_exception(type(exc), exc, exc.__traceback__))[-6000:]
    try:
        with _lock, open(ERRORS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass
