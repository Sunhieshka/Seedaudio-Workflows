"""App-wide model choice: which Seed LLM writes prompts and which Seedream draws images.

Every tool reads the current choice through prompt_model() / image_model() at
call time, so a change in the UI applies to the next request everywhere. The
choice is saved in OUTPUT_DIR/model_settings.json so it survives restarts.
"""

import json
import os
import threading

from content_creator.config.settings import OUTPUT_DIR

PROMPT_MODELS = [
    {"id": "dola-seed-2-1-turbo-260628", "label": "Seed 2.1 Turbo", "note": "fast, default"},
    {"id": "seed-2-0-pro-260328", "label": "Seed 2.0 Pro", "note": "strongest reasoning, slower"},
    {"id": "seed-2-0-lite-260428", "label": "Seed 2.0 Lite", "note": "balanced"},
    {"id": "seed-2-0-mini-260428", "label": "Seed 2.0 Mini", "note": "fastest, lightest"},
]
IMAGE_MODELS = [
    {"id": "dola-seedream-5-0-pro-260628", "label": "Seedream 5.0 Pro", "note": "best quality, default"},
    {"id": "dola-seedream-5-0-flash-260915", "label": "Seedream 5.0 Flash", "note": "faster, cheaper"},
]

SETTINGS_PATH = os.path.join(OUTPUT_DIR, "model_settings.json")
_lock = threading.Lock()
_cache: dict = {}


def _load() -> dict:
    if not _cache:
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            _cache.update(data if isinstance(data, dict) else {})
        except (OSError, ValueError):
            pass
        _cache.setdefault("prompt", PROMPT_MODELS[0]["id"])
        _cache.setdefault("image", IMAGE_MODELS[0]["id"])
    return _cache


def _known(model_id: str, options: list) -> bool:
    return any(o["id"] == model_id for o in options)


def prompt_model() -> str:
    with _lock:
        m = _load()["prompt"]
    return m if _known(m, PROMPT_MODELS) else PROMPT_MODELS[0]["id"]


def image_model() -> str:
    with _lock:
        m = _load()["image"]
    return m if _known(m, IMAGE_MODELS) else IMAGE_MODELS[0]["id"]


def config() -> dict:
    """Current choice plus the options, shaped for the UI's model pickers."""
    def entry(model_id: str, options: list) -> dict:
        o = next(x for x in options if x["id"] == model_id)
        return {"id": o["id"], "label": o["label"]}

    return {
        "prompt": entry(prompt_model(), PROMPT_MODELS),
        "image": entry(image_model(), IMAGE_MODELS),
        "options": {"prompt": PROMPT_MODELS, "image": IMAGE_MODELS},
    }


def set_models(prompt: str = "", image: str = "") -> dict:
    if prompt and not _known(prompt, PROMPT_MODELS):
        raise ValueError(f"Unknown LLM model: {prompt}")
    if image and not _known(image, IMAGE_MODELS):
        raise ValueError(f"Unknown image model: {image}")
    with _lock:
        data = _load()
        if prompt:
            data["prompt"] = prompt
        if image:
            data["image"] = image
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        tmp = SETTINGS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"prompt": data["prompt"], "image": data["image"]}, f, indent=1)
        os.replace(tmp, SETTINGS_PATH)
    return config()
