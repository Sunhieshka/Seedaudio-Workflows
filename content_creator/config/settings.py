import os
from pathlib import Path
from typing import Optional


PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = str(PROJECT_ROOT / "outputs")
# Per-project local folders for hand-picked "keep this one" generations — separate
# from OUTPUT_DIR's flat combine/skeleton output and from the BytePlus asset groups.
PROJECT_LOCAL_ROOT = str(PROJECT_ROOT / "outputs" / "projects")
DEFAULT_PORTS = tuple(
    int(part.strip())
    for part in os.getenv("CONTENT_CREATOR_PORTS", "8000").split(",")
    if part.strip()
) or (8000,)

ENV_DEFAULTS = {
    "TOS_REGION": "ap-southeast-1",
    "ARK_PROJECT": "default",
    "TOS_ENDPOINT": "",
    "TOS_PUBLIC_URL_PREFIX": "",
    "TOS_PREFIX": "ark-assets",
}

REQUIRED_ENV_VARS = (
    "ARK_API_KEY",
    "BYTEPLUS_AK",
    "BYTEPLUS_SK",
    "TOS_BUCKET_NAME",
    "TOS_REGION",
    "ARK_PROJECT",
    "ARK_MODEL_ENDPOINT",
)

OPTIONAL_ENV_VARS = (
    "CONTENT_CREATOR_PORTS",
    "TOS_ENDPOINT",
    "TOS_PUBLIC_URL_PREFIX",
    "TOS_PREFIX",
    "BYTEPLUS_ACCESSKEY",
    "BYTEPLUS_SECRETKEY",
    "BYTEPLUS_ACCESS_KEY",
    "BYTEPLUS_SECRET_KEY",
)


def get_env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(name, default)
    if value is None:
        return None
    value = value.strip()
    return value or default


def get_required_env(name: str) -> str:
    value = get_env(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def get_defaulted_env(name: str) -> str:
    return get_env(name, ENV_DEFAULTS[name]) or ENV_DEFAULTS[name]


def validate_environment(include_optional_runtime: bool = False) -> None:
    names = REQUIRED_ENV_VARS if include_optional_runtime else tuple(n for n in REQUIRED_ENV_VARS if n != "ARK_API_KEY")
    missing = [name for name in names if not get_env(name)]
    if missing:
        raise RuntimeError("Missing required environment variables: " + ", ".join(missing))


os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(PROJECT_LOCAL_ROOT, exist_ok=True)
