import base64
import mimetypes
import os
import json
import urllib.request
from typing import Union

from dotenv import load_dotenv
from content_creator.utils.llm_images import fit_all_for_llm, fit_for_llm
from byteplussdkarkruntime import Ark
from byteplussdkarkruntime.types.images.images import OptimizePromptOptions

from content_creator.config.settings import PROJECT_ROOT
from content_creator.services.model_settings import image_model, prompt_model

load_dotenv()

SEEDREAM_OPTIMIZE_OPTIONS = OptimizePromptOptions(mode="standard")
DEBUG_SERVER_URL = os.getenv("DEBUG_SERVER_URL", "")
DEBUG_SESSION_ID = os.getenv("DEBUG_SESSION_ID", "asset-url-seq-image")


def load_env() -> None:
    load_dotenv(dotenv_path=os.path.join(PROJECT_ROOT, ".env"))


def generate_character_image_from_text(*, prompt: str) -> str:
    ark = Ark()
    resp = ark.images.generate(
        model=image_model(),
        prompt=prompt,
        response_format="b64_json",
        watermark=False,
        output_format="png",
        optimize_prompt=True,
        optimize_prompt_options=SEEDREAM_OPTIMIZE_OPTIONS,
    )

    data = getattr(resp, "data", None) or []
    for img in data:
        b64 = getattr(img, "b64_json", None)
        if b64:
            return f"data:image/png;base64,{b64}"
    return ""


def generate_character_image_from_i2i(*, prompt: str, image_data_urls: Union[list[str], str]) -> str:
    if isinstance(image_data_urls, str):
        image_data_urls = [image_data_urls]
    ark = Ark()
    resp = ark.images.generate(
        model=image_model(),
        prompt=prompt,
        image=image_data_urls,
        response_format="b64_json",
        watermark=False,
        output_format="png",
        optimize_prompt=True,
        optimize_prompt_options=SEEDREAM_OPTIMIZE_OPTIONS,
    )

    data = getattr(resp, "data", None) or []
    for img in data:
        b64 = getattr(img, "b64_json", None)
        if b64:
            return f"data:image/png;base64,{b64}"
    return ""


def _guess_mime(filename: str) -> str:
    mime, _ = mimetypes.guess_type(filename)
    return mime or "application/octet-stream"


def to_data_url(filename: str, raw_bytes: bytes) -> str:
    mime = _guess_mime(filename)
    b64 = base64.b64encode(raw_bytes).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _extract_output_text(resp) -> str:
    chunks: list[str] = []
    output = getattr(resp, "output", None) or []
    for item in output:
        if getattr(item, "type", None) != "message":
            continue
        for content in getattr(item, "content", None) or []:
            if getattr(content, "type", None) == "output_text":
                chunks.append(getattr(content, "text", "") or "")
    return "".join(chunks).strip()


# #region debug-point B:image-url-format
def _report_debug_event(hypothesis_id: str, msg: str, data: dict) -> None:
    if not DEBUG_SERVER_URL:
        return
    try:
        urllib.request.urlopen(
            urllib.request.Request(
                DEBUG_SERVER_URL,
                data=json.dumps(
                    {
                        "sessionId": DEBUG_SESSION_ID,
                        "runId": "pre-fix",
                        "hypothesisId": hypothesis_id,
                        "location": "content_creator/services/characters.py",
                        "msg": msg,
                        "data": data,
                    }
                ).encode(),
                headers={"Content-Type": "application/json"},
            )
        ).read()
    except Exception:
        pass
# #endregion


# #region debug-point A:image-url-mime
def _data_url_debug_summary(data_url: str) -> dict:
    value = str(data_url or "")
    head = value[:80]
    mime = ""
    if value.startswith("data:") and ";base64," in value:
        mime = value[5:].split(";", 1)[0]
    return {
        "is_data_url": value.startswith("data:"),
        "mime": mime,
        "prefix": head,
        "length": len(value),
    }
# #endregion


def _call_prompt_model(reference_image_data_urls: list[str], instruction_text: str) -> str:
    ark = Ark()
    content = [{"type": "input_text", "text": instruction_text}]
    _report_debug_event(
        "A",
        "[DEBUG] Prompt model reference image formats",
        {"images": [_data_url_debug_summary(x) for x in reference_image_data_urls]},
    )
    # Seed LLM rejects images over 10 MiB; oversized ones are downscaled for this call only.
    for data_url in fit_all_for_llm(reference_image_data_urls):
        content.append({"type": "input_image", "detail": "high", "image_url": data_url})

    resp = ark.responses.create(
        model=prompt_model(),
        input=[{"role": "user", "content": content}],
        temperature=0.2,
    )
    return _extract_output_text(resp)


def _call_image_model(reference_image_data_urls: list[str], prompt: str) -> list[str]:
    ark = Ark()
    _report_debug_event(
        "A",
        "[DEBUG] Image model reference image formats",
        {"images": [_data_url_debug_summary(x) for x in reference_image_data_urls]},
    )
    # #region debug-point A:character-sheet-image-generate-args
    _report_debug_event(
        "A",
        "[DEBUG] Character sheet image generate args",
        {
            "model": image_model(),
            "has_images": bool(reference_image_data_urls),
            "image_count": len(reference_image_data_urls),
            "uses_sequential_image_generation": False,
            "optimize_prompt": True,
        },
    )
    # #endregion
    resp = ark.images.generate(
        model=image_model(),
        prompt=prompt,
        image=reference_image_data_urls if reference_image_data_urls else None,
        response_format="b64_json",
        watermark=False,
        output_format="png",
        optimize_prompt=True,
        optimize_prompt_options=SEEDREAM_OPTIMIZE_OPTIONS,
    )

    images: list[str] = []
    data = getattr(resp, "data", None) or []
    for img in data:
        b64 = getattr(img, "b64_json", None)
        if b64:
            images.append(f"data:image/png;base64,{b64}")
    return images


COMBINED_SHEET_INSTRUCTION = (
    "Given the portrait references, write a single prompt to generate one combined character sheet image for an AI-native film. "
    "Preserve all important details exactly: facial structure, facial features, skin tone, hairstyle, body proportions, outfit materials, colors, accessories, "
    "and overall style. The output must be one single reference sheet that combines: one face close-up portrait, one full-body straight front view, "
    "one full-body side view, and one full-body back view of the same character. "
    "All full-body views must be head-to-toe with consistent identity, consistent costume, consistent proportions, and clean straight-angle presentation. "
    "Arrange the sheet cleanly so the face close-up and the three full-body views are all clearly visible in one image. "
    "Use a pure white studio background, clean even lighting, sharp focus, and a professional reference-sheet layout. "
    "No text, no logos, no watermark, no extra props, no extra characters. Output prompt only."
)


def generate_character_sheets(reference_image_data_urls: list[str], _selected: list[str]) -> dict[str, str]:
    prompt = _call_prompt_model(reference_image_data_urls, COMBINED_SHEET_INSTRUCTION)
    generated = _call_image_model(reference_image_data_urls, prompt)
    return {"instruction_1": generated[0] if generated else ""}


def generate_three_images(reference_image_data_urls: list[str]) -> dict[str, str]:
    return generate_character_sheets(reference_image_data_urls, ["instruction_1"])
