"""Seedream 5.0 studio tools: image generation, layer decomposition, interactive editing.

Python port of the proxy in Documents/seedream-layer-seperation/seedream-layer-studio
(server.py), folded into this app's server:

- Requests go to ModelArk's /images/generations with the app's ARK key; the key
  never reaches the browser.
- Images the page references as /files/<name> (uploads, earlier results) are sent
  to ModelArk as data URIs, so the browser never has to hold or post them twice.
- Every returned image is copied into OUTPUT_DIR and given a `local_url`. ModelArk
  URLs expire after 24 hours, and its CDN sends no CORS header (a canvas that draws
  one can't be exported); /files/ URLs are permanent and same-origin.
"""

import base64
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Tuple

from dotenv import load_dotenv

from content_creator.config.settings import OUTPUT_DIR, PROJECT_ROOT, get_env

load_dotenv(dotenv_path=os.path.join(PROJECT_ROOT, ".env"))

MODELS = {
    "pro": "dola-seedream-5-0-pro-260628",
    "flash": "dola-seedream-5-0-flash-260915",
}
DEFAULT_BASE_URL = "https://ark.ap-southeast.bytepluses.com/api/v3"
# Layer decomposition renders a base plus up to 16 layers in one call and
# routinely runs past 90 s.
GENERATE_TIMEOUT_S = 15 * 60
DOWNLOAD_TIMEOUT_S = 300
MAX_INPUT_IMAGES = 14  # Seedream 5.0 accepts up to 14 reference images
ALLOWED_FIELDS = {
    "model", "prompt", "image", "size", "output_format", "response_format", "watermark",
    "layer_decomposition", "background", "optimize_prompt_options", "seed",
    "sequential_image_generation", "sequential_image_generation_options",
}


def _api_key() -> str:
    return get_env("SEEDREAM_API_KEY") or get_env("ARK_API_KEY") or ""


def _base_url() -> str:
    return (get_env("SEEDREAM_BASE_URL", DEFAULT_BASE_URL) or DEFAULT_BASE_URL).rstrip("/")


def config() -> dict:
    return {"configured": bool(_api_key()), "models": MODELS, "default_model": MODELS["pro"]}


# ---- local files <-> data URIs -------------------------------------------------

_EXT_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def save_image(data_url: str, hint: str = "upload") -> str:
    """Stores a data: image and returns its /files/ URL."""
    header, _, b64 = data_url.partition(",")
    if not header.startswith("data:image/") or not b64:
        raise ValueError("Expected a base64 data:image/... URL")
    mime = header[5:].split(";", 1)[0].lower()
    ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/webp": ".webp"}.get(mime, ".png")
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in hint)[:40].strip("-") or "upload"
    name = f"seedream-{safe}-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}{ext}"
    with open(os.path.join(OUTPUT_DIR, name), "wb") as f:
        f.write(base64.b64decode(b64))
    return "/files/" + name


def _resolve(ref: str) -> str:
    if not isinstance(ref, str):
        raise ValueError("image entries must be strings")
    if ref.startswith("/files/"):
        name = os.path.basename(ref[len("/files/"):].split("?")[0])
        path = os.path.join(OUTPUT_DIR, name)
        if not name or not os.path.isfile(path):
            raise ValueError(f"Image not found on the server: {name or ref}")
        mime = _EXT_MIME.get(os.path.splitext(name)[1].lower(), "image/png")
        with open(path, "rb") as f:
            # The API wants a lowercase format: data:image/png;base64,...
            return f"data:{mime};base64," + base64.b64encode(f.read()).decode("ascii")
    if ref.startswith(("data:image/", "http://", "https://")):
        return ref
    raise ValueError("Images must be data:image URLs, http(s) URLs, or /files/ paths")


# ---- generation ------------------------------------------------------------------

def _keep_copy(item: dict, index: int, stamp: str) -> None:
    url = item.get("url")
    if not url:
        return
    fmt = str(item.get("output_format") or "png").lower()
    ext = ".jpg" if fmt in ("jpeg", "jpg") else ".png"
    name = f"seedream-{stamp}-{index:02d}{ext}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT_S) as resp:
            raw = resp.read()
        with open(os.path.join(OUTPUT_DIR, name), "wb") as f:
            f.write(raw)
        item["local_url"] = "/files/" + name
    except Exception as e:  # keep the remote URL; the page can still show it for 24 h
        item["local_url"] = ""
        item["local_error"] = str(e)


def generate(body: dict) -> Tuple[int, dict]:
    key = _api_key()
    if not key:
        return 401, {"error": {"message": "No ARK API key. Set ARK_API_KEY (or SEEDREAM_API_KEY) in .env and restart."}}
    unknown = set(body) - ALLOWED_FIELDS
    if unknown:
        return 400, {"error": {"message": f"Unsupported field(s): {', '.join(sorted(unknown))}"}}

    body = dict(body)
    model = str(body.get("model") or "").strip()
    body["model"] = MODELS.get(model, model) or MODELS["pro"]
    body["response_format"] = "url"
    image = body.get("image")
    if isinstance(image, list):
        if len(image) > MAX_INPUT_IMAGES:
            return 400, {"error": {"message": f"At most {MAX_INPUT_IMAGES} input images."}}
        body["image"] = [_resolve(x) for x in image]
        if len(body["image"]) == 1:
            body["image"] = body["image"][0]
    elif image:
        body["image"] = _resolve(image)
    else:
        body.pop("image", None)

    req = urllib.request.Request(
        f"{_base_url()}/images/generations",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=GENERATE_TIMEOUT_S) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except ValueError:
        data = {"error": {"message": raw.decode("utf-8", "replace")[:1500] or f"HTTP {status}"}}
    if not (200 <= status < 300):
        err = data.get("error")
        if not isinstance(err, dict):
            data = {"error": {"message": str(err or data)[:1500]}}
        return status, data

    items = [x for x in (data.get("data") or []) if isinstance(x, dict)]
    stamp = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}"
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda pair: _keep_copy(pair[1], pair[0], stamp), enumerate(items)))
    return status, data
