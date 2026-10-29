import base64
import math
import os
import re
import subprocess
import tempfile
import time
import uuid
from typing import Optional
from urllib.parse import urlparse

import tos
from dotenv import load_dotenv

from byteplussdkcore.api_client import ApiClient
from byteplussdkcore.configuration import Configuration
from byteplussdkcore.universal import UniversalApi, UniversalInfo
from content_creator.models.asset import AssetEnv

load_dotenv()

# Longest presigned URL TOS allows (7 days).
PRESIGNED_URL_SECONDS = 7 * 24 * 3600


def _get_env_any(*names: str) -> str:
    for name in names:
        value = os.environ.get(name) or ""
        if value.strip():
            return value.strip()
    return ""


def _require_env_any(*names: str) -> str:
    value = _get_env_any(*names)
    if not value:
        raise RuntimeError(f"Missing required env var: {', '.join(names)}")
    return value


def load_asset_env() -> AssetEnv:
    ak = _require_env_any("BYTEPLUS_AK")
    sk = _require_env_any("BYTEPLUS_SK")
    os.environ["BYTEPLUS_ACCESSKEY"] = ak
    os.environ["BYTEPLUS_SECRETKEY"] = sk

    region = (_get_env_any("TOS_REGION") or "ap-southeast-1").strip()
    project_name = (_get_env_any("ARK_PROJECT") or "default").strip()
    ark_model_endpoint = _get_env_any("ARK_MODEL_ENDPOINT")

    tos_region = (_get_env_any("TOS_REGION") or region).strip()
    tos_bucket = _require_env_any("TOS_BUCKET_NAME")
    tos_endpoint = _get_env_any("TOS_ENDPOINT")
    if not tos_endpoint:
        tos_endpoint = f"https://tos-{tos_region}.bytepluses.com"
    tos_prefix = (os.environ.get("TOS_PREFIX") or "ark-assets").strip().strip("/")

    tos_public_url_prefix = (os.environ.get("TOS_PUBLIC_URL_PREFIX") or "").strip().rstrip("/")
    if not tos_public_url_prefix:
        tos_public_url_prefix = tos_endpoint.rstrip("/") + "/" + tos_bucket

    return AssetEnv(
        region=region,
        project_name=project_name,
        ark_model_endpoint=ark_model_endpoint,
        tos_region=tos_region,
        tos_bucket=tos_bucket,
        tos_endpoint=tos_endpoint,
        tos_public_url_prefix=tos_public_url_prefix,
        tos_prefix=tos_prefix,
    )


def _new_universal_api(region: str) -> UniversalApi:
    cfg = Configuration()
    cfg.region = region
    client = ApiClient(cfg)
    return UniversalApi(client)


def _new_tos_client(env: AssetEnv) -> tos.TosClientV2:
    return tos.TosClientV2(
        _require_env_any("BYTEPLUS_ACCESSKEY", "BYTEPLUS_ACCESS_KEY", "BYTEPLUS_AK"),
        _require_env_any("BYTEPLUS_SECRETKEY", "BYTEPLUS_SECRET_KEY", "BYTEPLUS_SK"),
        env.tos_endpoint,
        env.tos_region,
    )


def ensure_group_folder(*, env: AssetEnv, group_id: str) -> str:
    folder_key = f"{env.tos_prefix}/{group_id}/".lstrip("/")
    client = _new_tos_client(env)
    client.put_object(env.tos_bucket, folder_key, content=b"")
    return folder_key


def create_asset_group(*, name: str, description: str, region: str, project_name: str) -> str:
    env = load_asset_env()
    api = _new_universal_api(region)
    info = UniversalInfo(
        method="POST",
        service="ark",
        version="2024-01-01",
        action="CreateAssetGroup",
        content_type="application/json",
    )
    resp = api.do_call(
        info,
        {
            "Name": name,
            "Description": description,
            "ProjectName": project_name,
        },
    )
    group_id = (resp or {}).get("Id") or ""
    if not group_id:
        raise RuntimeError(f"CreateAssetGroup returned no Id: {resp}")
    ensure_group_folder(env=env, group_id=group_id)
    return group_id


def create_asset(*, group_id: str, url: str, asset_type: str, region: str, project_name: str) -> str:
    api = _new_universal_api(region)
    info = UniversalInfo(
        method="POST",
        service="ark",
        version="2024-01-01",
        action="CreateAsset",
        content_type="application/json",
    )
    resp = api.do_call(
        info,
        {
            "GroupId": group_id,
            "URL": url,
            "AssetType": asset_type,
            "Moderation": {"Strategy": "Skip"},
            "ProjectName": project_name,
        },
    )
    asset_id = (resp or {}).get("Id") or ""
    if not asset_id:
        raise RuntimeError(f"CreateAsset returned no Id: {resp}")
    return asset_id


def _decode_data_url(data_url: str) -> tuple[bytes, str]:
    if not data_url.startswith("data:"):
        raise RuntimeError("Expected data URL for image input.")

    header, b64 = data_url.split(",", 1)
    if ";base64" not in header:
        raise RuntimeError("Only base64 data URLs are supported.")

    mime = header[5:].split(";", 1)[0] or "application/octet-stream"
    raw = base64.b64decode(b64)
    return raw, mime


def _safe_ext_from_mime(mime: str) -> str:
    m = mime.lower()
    if "png" in m:
        return ".png"
    if "jpeg" in m or "jpg" in m:
        return ".jpg"
    if "webp" in m:
        return ".webp"
    if "avif" in m:
        return ".avif"
    if "mp4" in m:
        return ".mp4"
    if "mpeg" in m or "mp3" in m:
        return ".mp3"
    if "wav" in m:
        return ".wav"
    if "m4a" in m or "mp4a" in m:
        return ".m4a"
    return ".bin"


# ModelArk CreateAsset rejects videos whose width*height falls outside this
# range (InvalidParameter.PixelCountTooSmall / PixelCountTooLarge).
_VIDEO_MIN_PIXELS = 407_696
_VIDEO_MAX_PIXELS = 8_295_044


def _probe_video_dims(ffmpeg: str, path: str) -> Optional[tuple[int, int]]:
    p = subprocess.run([ffmpeg, "-hide_banner", "-i", path], capture_output=True, text=True)
    m = re.search(r"Stream #.*?Video:.*?\b(\d{2,5})x(\d{2,5})\b", p.stderr)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _even(n: float) -> int:
    return max(2, int(round(n / 2)) * 2)


def fit_video_pixel_count(raw: bytes) -> bytes:
    """Rescale a video so width*height is within ModelArk's asset limits."""
    from content_creator.services.long_duration import get_ffmpeg_path, ffmpeg_missing_message

    ffmpeg = get_ffmpeg_path()
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "in.mp4")
        dst = os.path.join(tmp, "out.mp4")
        with open(src, "wb") as f:
            f.write(raw)

        if not ffmpeg:
            raise RuntimeError(ffmpeg_missing_message())
        dims = _probe_video_dims(ffmpeg, src)
        if not dims:
            return raw
        w, h = dims
        pixels = w * h
        if _VIDEO_MIN_PIXELS <= pixels <= _VIDEO_MAX_PIXELS:
            return raw

        if pixels < _VIDEO_MIN_PIXELS:
            # Upscale so the short side is 720px (e.g. 640x360 -> 1280x720).
            factor = max(720 / min(w, h), math.sqrt(_VIDEO_MIN_PIXELS / pixels) * 1.02)
        else:
            factor = math.sqrt(_VIDEO_MAX_PIXELS * 0.98 / pixels)
        new_w, new_h = _even(w * factor), _even(h * factor)

        cmd = [
            ffmpeg, "-y", "-hide_banner", "-i", src,
            "-vf", f"scale={new_w}:{new_h}:flags=lanczos,setsar=1",
            "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
            dst,
        ]
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode != 0 or not os.path.exists(dst):
            raise RuntimeError(
                f"Video is {w}x{h} ({pixels:,} px); ModelArk needs {_VIDEO_MIN_PIXELS:,}-{_VIDEO_MAX_PIXELS:,} px "
                f"and auto-resize failed: {p.stderr[-500:]}"
            )
        with open(dst, "rb") as f:
            return f.read()


def upload_data_url_to_tos(*, data_url: str, env: AssetEnv, key_hint: str, group_id: Optional[str] = None) -> str:
    raw, mime = _decode_data_url(data_url)
    if mime.lower().startswith("video/"):
        fitted = fit_video_pixel_count(raw)
        if fitted is not raw:
            raw, mime = fitted, "video/mp4"
    ext = _safe_ext_from_mime(mime)

    ts = time.strftime("%Y%m%d-%H%M%S")
    if group_id:
        obj_key = f"{env.tos_prefix}/{group_id}/{ts}-{uuid.uuid4().hex}-{key_hint}{ext}"
    else:
        obj_key = f"{env.tos_prefix}/{ts}-{uuid.uuid4().hex}-{key_hint}{ext}"
    obj_key = obj_key.lstrip("/")

    client = _new_tos_client(env)
    client.put_object(env.tos_bucket, obj_key, content=raw, content_type=mime)
    # The bucket may be private (public reads return 403 "InvalidPathAccess"), so the URL handed to
    # CreateAsset / Seed Audio is presigned for the TOS maximum of 7 days. Both download it right away;
    # afterwards the app only keeps asset IDs and local copies, never this URL.
    return client.pre_signed_url(tos.HttpMethodType.Http_Method_Get, env.tos_bucket, obj_key,
                                 expires=PRESIGNED_URL_SECONDS).signed_url


def upload_image_data_url_to_tos(*, data_url: str, env: AssetEnv, key_hint: str, group_id: Optional[str] = None) -> str:
    return upload_data_url_to_tos(data_url=data_url, env=env, key_hint=key_hint, group_id=group_id)


def create_group_and_assets_from_generated_images(
    *,
    images: dict[str, str],
    group_name: Optional[str] = None,
    group_desc: Optional[str] = None,
) -> dict:
    env = load_asset_env()

    name = group_name or f"character-sheet-{time.strftime('%Y%m%d-%H%M%S')}"
    desc = group_desc or "Generated character sheet assets"
    group_id = create_asset_group(name=name, description=desc, region=env.region, project_name=env.project_name)

    hint_map = {
        "instruction_1": "character-sheet",
    }
    asset_ids: dict[str, str] = {}
    asset_urls: dict[str, str] = {}

    for key, value in images.items():
        hint = hint_map.get(key, key)
        data_url = (value or "").strip()
        if not data_url:
            raise RuntimeError(f"Missing generated image for {key}")

        url = upload_image_data_url_to_tos(data_url=data_url, env=env, key_hint=hint, group_id=group_id)
        asset_id = create_asset(
            group_id=group_id,
            url=url,
            asset_type="Image",
            region=env.region,
            project_name=env.project_name,
        )
        asset_ids[key] = asset_id
        asset_urls[key] = url

    return {"group_id": group_id, "asset_ids": asset_ids, "asset_urls": asset_urls}


def create_assets_from_generated_images_in_group(
    *,
    images: dict[str, str],
    group_id: str,
) -> dict:
    env = load_asset_env()
    ensure_group_folder(env=env, group_id=group_id)

    hint_map = {
        "instruction_1": "character-sheet",
    }
    asset_ids: dict[str, str] = {}
    asset_urls: dict[str, str] = {}

    for key, value in images.items():
        hint = hint_map.get(key, key)
        data_url = (value or "").strip()
        if not data_url:
            raise RuntimeError(f"Missing generated image for {key}")

        url = upload_image_data_url_to_tos(data_url=data_url, env=env, key_hint=hint, group_id=group_id)
        asset_id = create_asset(
            group_id=group_id,
            url=url,
            asset_type="Image",
            region=env.region,
            project_name=env.project_name,
        )
        asset_ids[key] = asset_id
        asset_urls[key] = url

    return {"group_id": group_id, "asset_ids": asset_ids, "asset_urls": asset_urls}


def create_group_and_asset_from_data_url(
    *,
    data_url: str,
    group_name: Optional[str] = None,
    group_desc: Optional[str] = None,
    key_hint: str = "t2i",
    asset_type: str = "Image",
) -> dict:
    env = load_asset_env()
    name = group_name or f"t2i-{time.strftime('%Y%m%d-%H%M%S')}"
    desc = group_desc or "Text-to-image character asset"
    group_id = create_asset_group(name=name, description=desc, region=env.region, project_name=env.project_name)
    url = upload_data_url_to_tos(data_url=data_url, env=env, key_hint=key_hint, group_id=group_id)
    asset_id = create_asset(
        group_id=group_id,
        url=url,
        asset_type=asset_type,
        region=env.region,
        project_name=env.project_name,
    )
    return {"group_id": group_id, "asset_id": asset_id, "asset_url": url}


def create_asset_from_data_url(
    *,
    data_url: str,
    group_id: str,
    key_hint: str = "generated",
    asset_type: str = "Image",
) -> dict:
    env = load_asset_env()
    ensure_group_folder(env=env, group_id=group_id)
    url = upload_data_url_to_tos(data_url=data_url, env=env, key_hint=key_hint, group_id=group_id)
    asset_id = create_asset(
        group_id=group_id,
        url=url,
        asset_type=asset_type,
        region=env.region,
        project_name=env.project_name,
    )
    return {"group_id": group_id, "asset_id": asset_id, "asset_url": url}


def create_group_and_assets_from_data_urls(
    *,
    data_urls: list[str],
    group_name: Optional[str] = None,
    group_desc: Optional[str] = None,
    key_hint: str = "ref",
    asset_type: str = "Image",
) -> dict:
    env = load_asset_env()
    name = group_name or f"refs-{time.strftime('%Y%m%d-%H%M%S')}"
    desc = group_desc or "Reference assets"
    group_id = create_asset_group(name=name, description=desc, region=env.region, project_name=env.project_name)

    asset_ids: list[str] = []
    asset_urls: list[str] = []
    for idx, data_url in enumerate(data_urls):
        url = upload_data_url_to_tos(data_url=data_url, env=env, key_hint=f"{key_hint}-{idx+1}", group_id=group_id)
        asset_id = create_asset(
            group_id=group_id,
            url=url,
            asset_type=asset_type,
            region=env.region,
            project_name=env.project_name,
        )
        asset_ids.append(asset_id)
        asset_urls.append(url)

    return {"group_id": group_id, "asset_ids": asset_ids, "asset_urls": asset_urls}


def normalize_asset_url(value: str) -> str:
    v = (value or "").strip()
    if not v:
        return ""
    if v.startswith("asset://"):
        return v
    parsed = urlparse(v)
    if parsed.scheme in ("http", "https", "data"):
        return v
    return f"asset://{v}"
