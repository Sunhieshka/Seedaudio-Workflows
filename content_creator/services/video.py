import os
from typing import Optional

from dotenv import load_dotenv
from content_creator.utils.llm_images import fit_all_for_llm, fit_for_llm
from byteplussdkarkruntime import Ark

from content_creator.services.model_settings import prompt_model

load_dotenv()

VIDEO_MODEL_ID = "dreamina-seedance-2-5-260628"

# Selectable video generation models. Each entry carries the constraints the
# model actually enforces, so the API can reject an unsupported duration or
# resolution before spending a task on it.
VIDEO_MODELS: dict[str, dict] = {
    "dreamina-seedance-2-5-260628": {
        "label": "2.5",
        "max_duration": 30,
        "resolutions": ["480p", "720p", "1080p"],
        # Reference limits: images, videos (total seconds), audios (total seconds).
        "max_ref_images": 30,
        "max_ref_videos": 10,
        "max_ref_video_seconds": 30,
        "max_ref_audios": 10,
        "max_ref_audio_seconds": 30,
        # Draft mode: 480p preview, then a 1080p final from the draft's task id.
        "draft": True,
    },
    "dreamina-seedance-2-0-260128": {
        "label": "2.0",
        "max_duration": 15,
        "resolutions": ["480p", "720p", "1080p", "4k"],
        # Reference limits: images, videos (total seconds), audios (total seconds).
        "max_ref_images": 9,
        "max_ref_videos": 3,
        "max_ref_video_seconds": 15,
        "max_ref_audios": 3,
        "max_ref_audio_seconds": 15,
    },
    "dreamina-seedance-2-0-fast-260128": {
        "label": "2.0 fast",
        "max_duration": 15,
        "resolutions": ["480p", "720p"],
        # Reference limits: images, videos (total seconds), audios (total seconds).
        "max_ref_images": 9,
        "max_ref_videos": 3,
        "max_ref_video_seconds": 15,
        "max_ref_audios": 3,
        "max_ref_audio_seconds": 15,
    },
    "dreamina-seedance-2-0-mini-260615": {
        "label": "2.0 mini",
        "max_duration": 15,
        "resolutions": ["480p", "720p"],
        # Reference limits: images, videos (total seconds), audios (total seconds).
        "max_ref_images": 9,
        "max_ref_videos": 3,
        "max_ref_video_seconds": 15,
        "max_ref_audios": 3,
        "max_ref_audio_seconds": 15,
    },
}

DEFAULT_VIDEO_MODEL_ID = VIDEO_MODEL_ID
MIN_VIDEO_DURATION = 4
# Upper bound on how many videos one /api/video/start call may launch from a
# single prompt.
MAX_VIDEO_VARIANTS = 3

# Skills live in the project's skills/ folder, one folder per skill (skills/<name>/SKILL.md).
SKILLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "skills")


def _read_skill_doc(name: str) -> str:
    path = os.path.join(SKILLS_DIR, name, "SKILL.md")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


SD25_PE_SKILL = _read_skill_doc("sd25-pe")


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


def load_env() -> None:
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))


def _video_prompt_system_instruction(ref_count: int, role_lines: list[str], project_skills_block: str = "") -> str:
    role_block = "\n".join(role_lines) if role_lines else "No explicit per-image role mapping was provided."
    if ref_count:
        refs_line = (
            f"You are given {ref_count} reference image{'s' if ref_count != 1 else ''}.\n"
            "Preserve identity and style consistently across the entire video based on the reference images.\n"
        )
    else:
        refs_line = "No reference images are given; do not invent [Image N] references.\n"
    if project_skills_block:
        skills_rule = (
            "Use the SD25 prompt optimizer skill as the primary guide for prompt structure and task shaping. "
            "Also apply the PROJECT SKILLS listed after it (selected for this project) for craft — performance, dialogue, "
            "staging, camera language, ad structure and so on. Where they disagree about the prompt format Seedance 2.5 "
            "expects, the SD25 skill wins. Never output a skill's analysis or worksheet; only the final prompt.\n"
        )
    else:
        skills_rule = "Use only the SD25 prompt optimizer skill below for prompt optimization and task-shaping guidance.\n"
    return (
        "You are writing a single prompt for an AI-native film video generation model.\n"
        + refs_line +
        "Your goal is to structure the prompt as clearly and strongly as possible so the model can generate the best possible video result.\n"
        "Use the user's content to define the action, setting, camera, blocking, sound cues, dialogue, pacing, and visual continuity.\n"
        "If any dialogue is present, the character must clearly lip-sync with the dialogue.\n"
        + skills_rule +
        "Prefer a prompt that is concrete, chronological, visually legible, and easy for the generation model to execute.\n"
        "The final prompt should read like one polished production-ready prompt.\n"
        "Return one production-ready prompt only. No markdown, no quotes around the full answer, no explanations.\n\n"
        "Reference image role mapping:\n"
        f"{role_block}\n\n"
        "SD25 PROMPT OPTIMIZER SKILL:\n"
        f"{SD25_PE_SKILL}"
        + (f"\n\nPROJECT SKILLS (selected for this project):\n\n{project_skills_block}" if project_skills_block else "")
    )


def create_video_task(
    *,
    prompt: str,
    reference_images: list[str],
    first_frame_image: Optional[str] = None,
    last_frame_image: Optional[str] = None,
    reference_videos: Optional[list[str]] = None,
    reference_audios: Optional[list[str]] = None,
    draft_task_id: Optional[str] = None,
    ratio: Optional[str] = None,
    resolution: Optional[str] = None,
    duration: Optional[int] = None,
    frames_per_second: Optional[int] = None,
    seed: Optional[int] = None,
    model: Optional[str] = None,
    draft: bool = False,
) -> str:
    if draft_task_id:
        return create_final_from_draft(draft_task_id=draft_task_id, model=model)
    ark = Ark()
    content = [{"type": "text", "text": prompt}]
    if first_frame_image:
        content.append({"type": "image_url", "image_url": {"url": first_frame_image}, "role": "first_frame"})
    if last_frame_image:
        content.append({"type": "image_url", "image_url": {"url": last_frame_image}, "role": "last_frame"})
    for url in reference_images:
        content.append({"type": "image_url", "image_url": {"url": url}, "role": "reference_image"})
    for url in reference_audios or []:
        content.append({"type": "audio_url", "audio_url": {"url": url}, "role": "reference_audio"})
    for url in reference_videos or []:
        content.append({"type": "video_url", "video_url": {"url": url}, "role": "reference_video"})

    effective_duration = duration

    task_kwargs = {
        "model": (model or "").strip() or DEFAULT_VIDEO_MODEL_ID,
        "content": content,
        "generate_audio": True,
        "return_last_frame": True,
        "resolution": resolution,
        "duration": effective_duration,
        "seed": seed,
        "watermark": False,
    }
    if not first_frame_image and not last_frame_image:
        task_kwargs["ratio"] = ratio or "16:9"
    if draft:
        # Draft mode: a quick 480p preview whose task id can later make the final video.
        task_kwargs["draft"] = True
        task_kwargs["resolution"] = DRAFT_RESOLUTION

    task = ark.content_generation.tasks.create(**task_kwargs)
    return getattr(task, "id", "")


# Draft mode (two-step generation). Step 1 renders a 480p draft; step 2 turns the
# draft's task id into the final video. The final task reuses the draft's prompt,
# references, duration, ratio, seed and audio setting by itself — sending any of
# them again is an error — so only the model, the draft id and the resolution go.
DRAFT_RESOLUTION = "480p"
DRAFT_FINAL_RESOLUTION = "1080p"
DRAFT_VALID_DAYS = 7


def create_final_from_draft(*, draft_task_id: str, model: Optional[str] = None) -> str:
    ark = Ark()
    task = ark.content_generation.tasks.create(
        model=(model or "").strip() or DEFAULT_VIDEO_MODEL_ID,
        content=[{"type": "draft_task", "draft_task": {"id": draft_task_id}}],
        resolution=DRAFT_FINAL_RESOLUTION,
        return_last_frame=True,
        watermark=False,
    )
    return getattr(task, "id", "")


def generate_video_prompt(*, ui_content: str, image_1: str, image_2: str, image_3: str) -> str:
    ark = Ark()
    role_lines = [
        "Image 1: close-up identity reference (face/identity).",
        "Image 2: full-body reference (outfit/physique).",
        "Image 3: pose/motion reference.",
    ]
    instruction = _video_prompt_system_instruction(3, role_lines)

    content = [
        {"type": "input_text", "text": f"User content: {ui_content.strip()}"},
        {"type": "input_image", "detail": "high", "image_url": fit_for_llm(image_1)},
        {"type": "input_image", "detail": "high", "image_url": fit_for_llm(image_2)},
        {"type": "input_image", "detail": "high", "image_url": fit_for_llm(image_3)},
    ]

    resp = ark.responses.create(
        model=prompt_model(),
        input=[
            {"role": "system", "content": instruction},
            {"role": "user", "content": content},
        ],
        temperature=0.2,
    )
    return _extract_output_text(resp)


def generate_video_prompt_dynamic(*, ui_content: str, images: list[str], roles: Optional[list[str]] = None) -> str:
    return refine_video_prompt(ui_content=ui_content, images=images, roles=roles)[0]


def refine_video_prompt(*, ui_content: str, images: list[str], roles: Optional[list[str]] = None,
                        skill_ids: Optional[list[str]] = None) -> tuple:
    """Seed LLM prompt optimisation: the Seedance 2.5 optimizer always, plus the
    project's selected skills. Runs with or without reference images.
    Returns (prompt, applied_skills)."""
    from content_creator.services.skills import ALWAYS_ON, project_skill_block

    refs = [str(x).strip() for x in (images or []) if str(x).strip()]
    if not (ui_content or "").strip() and not refs:
        return "", []
    skills = project_skill_block(skill_ids or [])
    applied = [{"id": ALWAYS_ON, "name": "Seedance 2.5 Optimizer", "mode": "full"}] + skills["applied"]

    role_lines: list[str] = []
    if roles and isinstance(roles, list):
        for idx, role in enumerate(roles[: len(refs)]):
            role_text = str(role).strip()
            if role_text:
                role_lines.append(f"Image {idx+1}: {role_text}.")
    if not role_lines:
        for idx in range(len(refs)):
            role_lines.append(f"Image {idx+1}: reference image.")

    instruction = _video_prompt_system_instruction(len(refs), role_lines, skills["block"])

    content = [{"type": "input_text", "text": f"User content: {(ui_content or '').strip()}"}]
    # Seed LLM rejects images over 10 MiB (e.g. an asset group original); it only
    # needs to see them, so oversized ones are downscaled for this call. Seedance
    # still receives the original asset.
    for url in fit_all_for_llm(refs):
        content.append({"type": "input_image", "detail": "high", "image_url": url})

    ark = Ark()
    resp = ark.responses.create(
        model=prompt_model(),
        input=[
            {"role": "system", "content": instruction},
            {"role": "user", "content": content},
        ],
        temperature=0.2,
    )
    return (_extract_output_text(resp) or (ui_content or "").strip()), applied


def get_video_task(*, task_id: str) -> dict:
    ark = Ark()
    task = ark.content_generation.tasks.get(task_id=task_id)
    content = getattr(task, "content", None)
    error = getattr(task, "error", None)

    return {
        "id": getattr(task, "id", ""),
        "status": getattr(task, "status", ""),
        "video_url": getattr(content, "video_url", "") if content else "",
        "last_frame_url": getattr(content, "last_frame_url", "") if content else "",
        "file_url": getattr(content, "file_url", "") if content else "",
        "revised_prompt": getattr(task, "revised_prompt", "") or "",
        "error": {
            "code": getattr(error, "code", "") if error else "",
            "message": getattr(error, "message", "") if error else "",
        },
    }
