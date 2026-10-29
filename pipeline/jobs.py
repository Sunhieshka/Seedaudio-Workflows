"""Background jobs for long steps (script breakdown, location plates, redub).

A job runs one function on a thread; the page polls /api/jobs/status until it is
done. Jobs live in memory, so a server restart ends them.
"""

import threading
import time
import traceback
import uuid
from typing import Callable, Optional

from content_creator.utils import errorlog

_lock = threading.Lock()
_jobs: dict = {}


def start(kind: str, fn: Callable[..., dict], context: Optional[dict] = None) -> str:
    """Runs fn(progress) in the background; fn returns the job's result dict. A failure is written to
    outputs/errors.log with `context` (project, part, shots…)."""
    job_id = uuid.uuid4().hex[:12]
    with _lock:
        _jobs[job_id] = {"id": job_id, "kind": kind, "status": "running", "stage": "Starting",
                         "result": None, "error": "", "created": time.time()}

    def progress(stage: str, **partial) -> None:
        """stage: what is happening now; partial: results so far, merged into job["partial"]."""
        with _lock:
            _jobs[job_id]["stage"] = stage
            if partial:
                _jobs[job_id].setdefault("partial", {}).update(partial)

    def run() -> None:
        try:
            result = fn(progress)
            with _lock:
                _jobs[job_id].update(status="done", stage="Done", result=result)
        except Exception as e:
            traceback.print_exc()
            errorlog.log(f"job:{kind}", str(e), exc=e, job_id=job_id, stage=_jobs[job_id].get("stage"), **(context or {}))
            with _lock:
                _jobs[job_id].update(status="failed", error=str(e) or e.__class__.__name__)

    threading.Thread(target=run, daemon=True).start()
    return job_id


def get(job_id: str) -> Optional[dict]:
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None
