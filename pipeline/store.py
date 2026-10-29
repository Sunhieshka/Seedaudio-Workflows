"""Projects are saved whole, as the page sends them, in outputs/projects.json."""

import json
import os
import threading
import time

from content_creator.config.settings import OUTPUT_DIR

PROJECTS_PATH = os.path.join(OUTPUT_DIR, "projects.json")
_lock = threading.Lock()


def _load() -> dict:
    try:
        with open(PROJECTS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(data: dict) -> None:
    tmp = PROJECTS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, PROJECTS_PATH)


def list_projects() -> list:
    with _lock:
        data = _load()
    rows = [{"id": p["id"], "name": p.get("name") or "Untitled", "updated": p.get("updated") or 0,
             "shots": len(p.get("shots") or [])} for p in data.values() if isinstance(p, dict) and p.get("id")]
    return sorted(rows, key=lambda r: r["updated"], reverse=True)


def get_project(project_id: str):
    with _lock:
        return _load().get(project_id)


def save_project(project: dict) -> dict:
    if not isinstance(project, dict) or not str(project.get("id") or "").strip():
        raise ValueError("project.id is required")
    project["updated"] = int(time.time() * 1000)
    with _lock:
        data = _load()
        data[project["id"]] = project
        _write(data)
    return {"id": project["id"], "updated": project["updated"]}


def delete_project(project_id: str) -> None:
    with _lock:
        data = _load()
        data.pop(project_id, None)
        _write(data)
