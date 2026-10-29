"""Character Generation and the QA-checked Character Sheet pipeline.

Ported from the Character Sheet Studio workflow (Documents/character-sheet-workflow), adapted to
this app's inputs (reference images; with no images the notes are used as a text description, exactly as in the workflow):

  references (+ notes, + bible from Character Generation)
    -> [Seed LLM, vision] character bible + Seedream prompt -> [Seed LLM] contradiction review
    -> [Seed LLM] grouped QA checklist   ||   [Seedream 5.0] attempt 1 (references + prompt)
    -> inspection: image-only continuity audit (Seed LLM vision) -> software converts positions to
       body sides -> checklist inspection against the sheet and the references -> 16:9 measured in code
    -> passed? deliver : ONE retry (edit the sheet, or regenerate with a corrections block when the
       layout/ratio is wrong) -> inspect -> deliver the better attempt.

MAX_ATTEMPTS is 2: the first generation plus at most one retry.
"""

import base64
import json
import os
import re
import threading
import time
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from byteplussdkarkruntime import Ark

from content_creator.config.settings import OUTPUT_DIR
from content_creator.services import character_prompts as P
from content_creator.services.characters import _extract_output_text
from content_creator.services.model_settings import image_model, prompt_model
from content_creator.utils.llm_images import fit_all_for_llm, fit_for_llm

MAX_ATTEMPTS = 2                 # first generation + one retry
SHEET_SIZE = "2560x1440"         # 16:9 at 2K
SHEET_RATIO = (16, 9)
CHARACTER_SIZE = "1440x2560"     # one full-body character, head to shoes
MAX_SEEDREAM_REFS = 9

LLM_TIMEOUT_S = 180
IMAGE_TIMEOUT_S = 300
# Output caps per call: Seed LLM occasionally falls into a repetitive answer that would otherwise run
# until the timeout (and be retried); a capped answer fails fast and the step falls back.
TOKENS = {"bible": 7000, "review": 7000, "checklist": 3000, "audit": 5000, "inspect": 7000, "edit": 1500,
          "character": 3500}

_RETRYABLE = ("rate", "timeout", "timed out", "429", "500", "502", "503", "504", "server", "connection")


# ------------------------------------------------------------------ model calls

def _with_retries(fn, attempts: int = 2):
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            if i == attempts - 1 or not any(k in str(e).lower() for k in _RETRYABLE):
                raise
            time.sleep(2 ** (i + 1))


def _parse_json(text: str) -> dict:
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("Seed LLM did not return JSON: " + text[:300])
        return json.loads(text[start:end + 1])


def llm_json(system: str, user: str, images: Optional[list] = None, temperature: float = 0.2,
             max_tokens: int = 6000, retries: int = 2) -> dict:
    """Seed LLM (text or vision) -> parsed JSON object, with one repair turn for invalid JSON."""
    content = [{"type": "input_text", "text": user}]
    for url in fit_all_for_llm([u for u in (images or []) if u]):
        content.append({"type": "input_image", "detail": "high", "image_url": url})
    messages = [{"role": "system", "content": system}, {"role": "user", "content": content}]
    last = None
    for _ in range(2):
        resp = _with_retries(lambda: Ark(timeout=LLM_TIMEOUT_S).responses.create(
            model=prompt_model(), input=messages, temperature=temperature, thinking={"type": "disabled"},
            max_output_tokens=max_tokens), attempts=retries)
        reply = _extract_output_text(resp)
        try:
            data = _parse_json(reply)
            if isinstance(data, dict):
                return data
        except (ValueError, json.JSONDecodeError) as e:
            last = e
        messages = messages + [{"role": "assistant", "content": reply or "(empty)"},
                               {"role": "user", "content": "That was not valid JSON. Reply again with ONLY the JSON object."}]
    raise RuntimeError(f"Seed LLM returned invalid JSON: {last}")


def _save_image(url: str, prefix: str) -> dict:
    """Seedream URLs expire after ~24 h, so every image is saved to outputs/."""
    name = f"{prefix}-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}.png"
    path = os.path.join(OUTPUT_DIR, name)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=180) as resp, open(path, "wb") as f:
        f.write(resp.read())
    return {"file": "/files/" + name, "path": path}


def seedream(prompt: str, images: Optional[list] = None, size: str = SHEET_SIZE, prefix: str = "sheet") -> dict:
    """Seedream 5.0 text-to-image, or reference/edit generation when images are given."""
    extra = {"image": images[:MAX_SEEDREAM_REFS]} if images else {}
    resp = _with_retries(lambda: Ark(timeout=IMAGE_TIMEOUT_S).images.generate(
        model=image_model(), prompt=prompt, size=size, response_format="url",
        output_format="png", watermark=False, **extra))
    data = getattr(resp, "data", None) or []
    if not data or getattr(data[0], "error", None) or not getattr(data[0], "url", None):
        raise RuntimeError(f"Seedream returned no image: {getattr(data[0], 'error', '') if data else 'empty response'}")
    saved = _save_image(data[0].url, prefix)
    return {"url": data[0].url, "size": getattr(data[0], "size", None), **saved}


def data_url_of(path: str) -> str:
    with open(path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode("ascii")


def _png_dimensions(path: Optional[str]):
    try:
        with open(path, "rb") as f:
            header = f.read(24)
    except (OSError, TypeError):
        return None
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    w, h = int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")
    return (w, h) if w > 0 and h > 0 else None


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "yes", "pass", "passed", "1"}


# ------------------------------------------------------------------ Character Generation

def engineer_character(request: str, images: Optional[list] = None) -> dict:
    """Seed LLM turns a character request (plus optional source images) into a locked bible and a
    prompt for ONE reference-ready, full-body character image."""
    note = P.I2I_MODE_NOTE if images else P.T2I_MODE_NOTE
    data = llm_json(P.CHARACTER_ENGINEER_SYSTEM.format(mode_note=note),
                    "CHARACTER REQUEST:\n" + request.strip(), images=images, temperature=0.5,
                    max_tokens=TOKENS["character"])
    prompt = str(data.get("image_prompt") or "").strip()
    if not prompt:
        raise RuntimeError("Seed LLM returned no image prompt.")
    if "neutral" not in prompt.lower():
        prompt += " Flat neutral expression with the mouth closed."
    bible = data.get("character_bible") if isinstance(data.get("character_bible"), dict) else {}
    return {"prompt": prompt, "bible": bible}


def generate_character(request: str, images: Optional[list] = None) -> dict:
    """Reference-ready character image: engineered prompt -> Seedream (portrait, full body)."""
    eng = engineer_character(request, images)
    img = seedream(eng["prompt"], images=images or None, size=CHARACTER_SIZE, prefix="character")
    return {"image": data_url_of(img["path"]), "file": img["file"], "prompt": eng["prompt"], "bible": eng["bible"]}


# ------------------------------------------------------------------ sheet pipeline steps

def build_sheet_prompt(refs: list, notes: str, prior_bible: Optional[dict]) -> dict:
    if not refs:
        # No reference images: the notes are the character description, as in the Character Sheet Studio
        # workflow (bible + prompt from text, photorealistic, Seedream text-to-image).
        data = llm_json(P.TEXT_SHEET_ENGINEER_SYSTEM,
                        P.TEXT_SHEET_ENGINEER_USER.format(description=notes.strip(), base_prompt=P.TEXT_SHEET_BASE_PROMPT),
                        temperature=0.7, max_tokens=TOKENS["bible"])
        prompt = str(data.get("image_prompt") or "").strip()
        if not prompt:
            raise RuntimeError("Seed LLM returned no sheet prompt.")
        bible = data.get("character_bible") if isinstance(data.get("character_bible"), dict) else {}
        return {"bible": bible, "prompt": prompt}
    data = llm_json(P.SHEET_ENGINEER_SYSTEM,
                    P.SHEET_ENGINEER_USER.format(notes=notes.strip() or "(none)",
                                                 prior_bible=_dump(prior_bible) if prior_bible else "(none)",
                                                 base_prompt=P.BASE_CHARACTER_SHEET_PROMPT),
                    images=refs, temperature=0.5, max_tokens=TOKENS["bible"])
    prompt = str(data.get("image_prompt") or "").strip()
    if not prompt:
        raise RuntimeError("Seed LLM returned no sheet prompt.")
    bible = data.get("character_bible") if isinstance(data.get("character_bible"), dict) else (prior_bible or {})
    return {"bible": bible, "prompt": prompt}


def review_sheet_prompt(bible: dict, prompt: str, refs: Optional[list] = None) -> dict:
    """Fixes contradictions (a chest print on a waist apron…) before anything is generated; never blocks a run.
    It sees the references so a "fix" can never contradict them."""
    try:
        data = llm_json(P.PROMPT_REVIEW_SYSTEM, P.PROMPT_REVIEW_USER.format(bible=_dump(bible), prompt=prompt),
                        images=(refs or [])[:4], max_tokens=TOKENS["review"], retries=1)
    except Exception:
        return {"bible": bible, "prompt": prompt, "fixes": []}
    new_bible = data.get("character_bible") if isinstance(data.get("character_bible"), dict) else None
    new_prompt = str(data.get("image_prompt") or "").strip()
    if not new_bible or not new_prompt:
        return {"bible": bible, "prompt": prompt, "fixes": []}
    fixes = [str(i) for i in data.get("issues_fixed") or [] if str(i).strip()]
    fixes = [i for i in fixes if not any(k in i.lower() for k in P.EXPRESSION_KEYWORDS)]
    if "neutral" not in new_prompt.lower():
        new_prompt += " Flat neutral expression with the mouth closed and no smile in every panel."
    return {"bible": new_bible, "prompt": new_prompt, "fixes": fixes}


def build_checklist(bible: dict, prompt: str) -> list:
    guide = "\n".join(
        f"- {g['id']} ({g['name']}): add criteria about {g['hint']}. Standard criteria already present: "
        + " | ".join(c[0] for c in g["core"])
        for g in P.CHECK_GROUPS if g["character_criteria"])
    try:
        data = llm_json(P.CHECKLIST_SYSTEM.format(group_guide=guide),
                        P.CHECKLIST_USER.format(bible=_dump(bible), prompt=prompt),
                        max_tokens=TOKENS["checklist"], retries=1)
    except Exception:
        data = {}          # the standard criteria still apply
    right_hand = [str(x).strip() for x in data.get("RIGHT_HAND_ITEMS") or [] if str(x).strip()]
    groups, total_character = [], 0
    for spec in P.CHECK_GROUPS:
        criteria = []
        for i, (text, critical, key) in enumerate(spec["core"]):
            short = spec["short"][i]
            if key == "right_hand" and right_hand:
                text, short = P.RIGHT_HAND_WITH_ITEMS.format(items=", ".join(right_hand)), P.RIGHT_HAND_WITH_ITEMS_SHORT
            criteria.append({"text": text, "critical": critical, "source": "core", "key": key, "short": short})
        if spec["character_criteria"]:
            raw_items = data.get(spec["id"]) or []
            for raw in (raw_items if isinstance(raw_items, list) else [])[:5]:
                text = str(raw.get("check", "") if isinstance(raw, dict) else raw).strip()
                if not text or any(k in text.lower() for k in P.EXPRESSION_KEYWORDS) or total_character >= 18:
                    continue
                total_character += 1
                critical = _as_bool(raw.get("critical", True)) if isinstance(raw, dict) else True
                criteria.append({"text": text, "critical": critical, "source": "character", "key": "", "short": ""})
        for n, c in enumerate(criteria, 1):
            c["id"] = f"{spec['id']}.{n}"
        groups.append({"id": spec["id"], "name": spec["name"], "criteria": criteria})
    return groups


# ---- continuity analysis (deterministic, ported unchanged in behaviour)

_PAIR_WORDS = re.compile(
    r"\b(gloves|mittens|boots|shoes|sneakers|heels|loafers|socks|earrings|cufflinks|cuffs|sleeves|pair|both|"
    r"each (?:hand|ear|foot|wrist|side|arm|leg))\b", re.I)
_HAND_WORDS = re.compile(r"\b(held|holding|in (?:the |his |her |their )?(?:right )?hand|ring|watch|bracelet|glove)\b", re.I)
_FRONT_VIEW_SIDE = {"picture-left": "right", "picture-right": "left", "center": "center"}
_BACK_VIEW_SIDE = {"picture-left": "left", "picture-right": "right", "center": "center"}


def _image_side(value) -> str:
    v = str(value or "").strip().lower().replace("_", "-").replace(" ", "-")
    if v in ("", "none", "n/a", "not-visible"):
        return "none"
    if "left" in v:
        return "picture-left"
    if "right" in v:
        return "picture-right"
    if "cent" in v or "middle" in v:
        return "center"
    return "none"


def _analyze_detail(raw: dict) -> dict:
    """Image positions -> the character's body sides, plus continuity issues."""
    detail = str(raw.get("detail", "detail"))
    surface = str(raw.get("surface", "")).lower()
    front = raw.get("front_view") if isinstance(raw.get("front_view"), dict) else {}
    back = raw.get("back_view") if isinstance(raw.get("back_view"), dict) else {}
    side = raw.get("side_view") if isinstance(raw.get("side_view"), dict) else {}
    front_visible, back_visible = _as_bool(front.get("visible")), _as_bool(back.get("visible"))
    front_img, back_img = _image_side(front.get("image_side")), _image_side(back.get("image_side"))
    front_body = _FRONT_VIEW_SIDE.get(front_img) if front_visible else None
    back_body = _BACK_VIEW_SIDE.get(back_img) if back_visible else None
    side_visible, carrier_visible = _as_bool(side.get("visible")), _as_bool(side.get("carrier_area_visible"))

    issues = []
    paired = bool(_PAIR_WORDS.search(detail))
    if not paired and front_body in ("left", "right") and back_body in ("left", "right") and front_body != back_body:
        issues.append(("details", f"switches sides: character's {front_body} in the front view but character's "
                                  f"{back_body} in the back view"))
    if surface == "front" and back_visible:
        issues.append(("back_visibility", "sits on the front of the body but is visible in the back view"))
    if surface == "back" and front_visible:
        issues.append(("details", "sits on the back of the body but is visible in the front view"))
    sides = {s for s in (front_body, back_body) if s}
    on_left = "left" in sides and "right" not in sides
    on_right = "right" in sides and "left" not in sides
    expected_in_side = on_left and (surface in ("side_or_top", "front", "back") or carrier_visible)
    if expected_in_side and not side_visible:
        issues.append(("side_visibility", "is on the character's left, which the side view shows, but it is missing there"))
    body_area = str(raw.get("body_area", "")).lower()
    in_right_hand = _as_bool(raw.get("held_in_hand")) or body_area in ("hand", "wrist") or bool(_HAND_WORDS.search(detail))
    if on_right and side_visible and not in_right_hand:
        issues.append(("side_visibility", "is on the character's right but appears in the side view"))
    if "same_content" in raw and not _as_bool(raw.get("same_content")):
        issues.append(("details", "looks different between views" + (f": {raw['note']}" if raw.get("note") else "")))

    def where(visible, img, body):
        if not visible:
            return "not visible"
        return f"{img} of the figure = character's {body}" if img != "none" else "visible (position not given)"

    facts = (f"surface={surface or '?'}; front view: " + where(front_visible, front_img, front_body)
             + "; side view: " + ("visible" if side_visible else "not visible")
             + "; back view: " + where(back_visible, back_img, back_body)
             + ("; CONSISTENT" if not issues else "; ISSUES: " + "; ".join(i for _, i in issues)))
    return {"detail": detail, "surface": surface,
            "body_location": ("character's " + "/".join(sorted(sides))) if sides else "not determined",
            "consistent": not issues, "issues": issues, "facts": facts}


def _audit_failures(audit: dict) -> dict:
    failures = {"garments": [], "layering": [], "details": [], "side_visibility": [], "back_visibility": []}
    for item in audit.get("garments") or []:
        if isinstance(item, dict) and not _as_bool(item.get("consistent", True)):
            failures["garments"].append(f"{item.get('region', 'region')}: {item.get('note') or 'garment differs between views'}")
    for item in audit.get("layering") or []:
        if isinstance(item, dict) and not _as_bool(item.get("consistent", True)):
            failures["layering"].append(f"{item.get('zone', 'overlap')}: {item.get('note') or 'layering differs between views'}")
    for item in audit.get("details") or []:
        for key, issue in item.get("issues", []):
            failures[key].append(f"{item['detail']}: {issue}")
    return {k: v for k, v in failures.items() if v}


def inspect(sheet: dict, refs: list, bible: dict, checklist: list) -> dict:
    sheet_url = sheet.get("url") or data_url_of(sheet["path"])
    # 1) Image-only continuity audit (raw positions), 2) software converts them to body sides.
    try:
        audit = llm_json(P.CONSISTENCY_AUDIT_SYSTEM, P.CONSISTENCY_AUDIT_USER, images=[sheet_url], temperature=0.0,
                         max_tokens=TOKENS["audit"], retries=1)
    except Exception:
        audit = {}
    audit = audit if isinstance(audit, dict) else {}
    audit["details"] = [_analyze_detail(d) for d in audit.get("details") or [] if isinstance(d, dict)]
    side_facts = "\n".join(f"- {d['detail']}: {d['facts']}" for d in audit["details"]) or "(none measured)"
    forced = _audit_failures(audit)
    # 3) Checklist inspection against the sheet and the references.
    checklist_text = "\n\n".join(
        f"GROUP {g['id']} ({g['name']})\n" + "\n".join(
            f"  - [{c['id']}]{' (critical)' if c['critical'] else ''} {c['text']}" for c in g["criteria"])
        for g in checklist)
    data = llm_json(P.VLM_CHECK_SYSTEM,
                    P.VLM_CHECK_USER.format(bible=_dump(bible), checklist=checklist_text, side_facts=side_facts),
                    images=[sheet_url] + list(refs[:4]), temperature=0.0, max_tokens=TOKENS["inspect"])
    # The model sometimes answers "GROUP FACE" or "FACE (Face & identity)"; criteria are matched by id
    # across all groups, so a renamed group never loses its verdicts.
    raw_groups, verdicts = {}, {}
    for rg in data.get("groups") or []:
        if not isinstance(rg, dict):
            continue
        gid = re.sub(r"^GROUP\s+", "", str(rg.get("id", "")).strip("[] ").upper()).split(" ")[0].strip("()")
        raw_groups[gid] = rg
        for c in rg.get("criteria") or []:
            if isinstance(c, dict):
                verdicts[str(c.get("id", "")).strip("[] ").upper()] = c
    groups = []
    for g in checklist:
        raw = raw_groups.get(g["id"], {})
        criteria, audit_fixes = [], []
        for c in g["criteria"]:
            v = verdicts.get(c["id"].upper())
            passed = _as_bool(v.get("passed", False)) if v else False     # unevaluated never passes
            note = str(v.get("note", "")) if v else "The inspector did not evaluate this criterion."
            if c["key"] and forced.get(c["key"]):
                issues = "; ".join(forced[c["key"]])
                note = (note + " " if not passed and note else "") + "Continuity audit: " + issues
                passed = False
                audit_fixes.append(issues)
            criteria.append({**c, "passed": passed, "note": "" if passed else note})
        ok = all(c["passed"] for c in criteria)
        fix = str(raw.get("fix", "") or "")
        if audit_fixes:
            fix = (fix + " " if fix else "") + "Make these consistent across the front, side and back views: " + \
                "; ".join(dict.fromkeys(audit_fixes)) + "."
        if not ok and not fix:
            fix = " ".join(c["text"] for c in criteria if not c["passed"])
        groups.append({"id": g["id"], "name": g["name"], "passed": ok, "observation": str(raw.get("observation", "")),
                       "fix": "" if ok else fix, "criteria": criteria})
    groups.append(_ratio_group(sheet))
    return {"groups": groups, "summary": str(data.get("summary", "")),
            "audit": {k: audit.get(k) for k in ("garments", "layering")} | {
                "details": [{k: d[k] for k in ("detail", "body_location", "consistent", "facts")} for d in audit["details"]]}}


def _ratio_group(sheet: dict) -> dict:
    """16:9 measured from the saved file, not judged by the model."""
    dims = _png_dimensions(sheet.get("path"))
    w, h = SHEET_RATIO
    if not dims:
        ok, obs = False, "Could not read the image size."
    else:
        ok = abs(dims[0] / dims[1] - w / h) <= 0.01 * w / h
        obs = f"Image is {dims[0]}×{dims[1]}."
    return {"id": "RATIO", "name": "Aspect ratio", "passed": ok, "observation": obs,
            "fix": "" if ok else f"Compose as a wide {w}:{h} landscape image.",
            "criteria": [{"id": "RATIO.1", "text": f"The image has a {w}:{h} aspect ratio.", "critical": True,
                          "source": "core", "key": "", "short": "16:9 aspect ratio", "passed": ok,
                          "note": "" if ok else obs}]}


def _score(attempt: dict):
    failed = [c for g in attempt["groups"] for c in g["criteria"] if not c["passed"]]
    return (-sum(1 for c in failed if c["critical"]), -len(failed))


def _failure_text(attempt: dict):
    failed = "\n\n".join(
        f"GROUP {g['id']} ({g['name']})\n" + "\n".join(
            f"  - FAILED [{c['id']}]{' (critical)' if c['critical'] else ''} {c['text']}\n    note: {c['note']}"
            for c in g["criteria"] if not c["passed"])
        + f"\n  observation: {g['observation']}\n  suggested fix: {g['fix']}"
        for g in attempt["groups"] if not g["passed"])
    passed = "\n".join(f"- [{c['id']}] {c['text']}" for g in attempt["groups"] for c in g["criteria"] if c["passed"]) or "(none)"
    return failed, passed


# ------------------------------------------------------------------ jobs

_jobs: dict = {}
_lock = threading.Lock()

STEPS = [("prompt", "Character bible & prompt"), ("checklist", "QA checklist"), ("generate", "Generate sheet"),
         ("inspect", "Inspect"), ("retry", "Retry (max 1)"), ("done", "Deliver")]


def get_job(job_id: str) -> Optional[dict]:
    with _lock:
        job = _jobs.get(job_id)
        return json.loads(json.dumps(job)) if job else None


def _update(job_id: str, fn) -> None:
    with _lock:
        fn(_jobs[job_id])


def _step(job_id: str, key: str, state: str) -> None:
    def apply(job):
        for s in job["steps"]:
            if s["key"] == key:
                s["state"] = state
                s["started" if state == "active" else "ended"] = time.time()
        active = [s["label"] for s in job["steps"] if s["state"] == "active"]
        job["stage"] = " + ".join(active) if active else job["stage"]
    _update(job_id, apply)


# Inputs of finished jobs, kept so "Inspect & retry" can run later on the same sheet.
_inputs: dict = {}


def start_sheet_job(refs: list, notes: str = "", prior_bible: Optional[dict] = None) -> str:
    """Bible -> prompt -> one sheet. QA (checklist, inspection, one retry) runs only on request."""
    job_id = uuid.uuid4().hex[:12]
    with _lock:
        _jobs[job_id] = {"id": job_id, "status": "running", "stage": "Starting", "error": "", "created": time.time(),
                         "steps": [{"key": k, "label": l, "state": "skipped" if k in ("checklist", "inspect", "retry") else "pending"}
                                   for k, l in STEPS],
                         "max_attempts": MAX_ATTEMPTS, "attempts": [], "bible": None, "prompt": "",
                         "review_fixes": [], "checklist": None, "result": None, "inspected": False}
    threading.Thread(target=_run_sheet, args=(job_id, refs, notes, prior_bible), daemon=True).start()
    return job_id


def start_inspect_job(job_id: str) -> None:
    """Checks a finished sheet against a QA checklist and retries once if anything fails."""
    with _lock:
        job = _jobs.get(job_id)
        inputs = _inputs.get(job_id)
        if not job or not inputs:
            raise ValueError("This sheet is no longer on the server (it restarted). Generate it again to inspect it.")
        if job["status"] == "running":
            raise ValueError("This sheet is still being worked on.")
        job.update(status="running", stage="Inspecting", error="")
        for st in job["steps"]:
            if st["key"] in ("checklist", "inspect", "retry", "done"):
                st["state"] = "pending"
    threading.Thread(target=_run_inspect, args=(job_id,), daemon=True).start()


def _fail(job_id: str, e: Exception) -> None:
    from content_creator.utils import errorlog
    errorlog.log("job:character-sheet", str(e), exc=e, job_id=job_id, stage=(_jobs.get(job_id) or {}).get("stage"))

    def fail(j):
        j.update(status="error", stage="Failed", error=str(e))
        for st in j["steps"]:
            if st["state"] == "active":
                st["state"] = "error"
    _update(job_id, fail)


def _finish(job_id: str, chosen: dict) -> None:
    result = {"attempt": chosen["number"], "passed": chosen.get("passed"), "image": chosen["image"],
              "image_data_url": data_url_of(chosen["path"]),
              "criteria_total": chosen.get("criteria_total", 0), "criteria_passed": chosen.get("criteria_passed", 0)}

    def finish(j):
        j.update(status="done", stage="Done", result=result)
        for st in j["steps"]:
            if st["state"] == "pending":
                st["state"] = "skipped"
    _update(job_id, finish)
    _step(job_id, "done", "done")


def _run_sheet(job_id: str, refs: list, notes: str, prior_bible: Optional[dict]) -> None:
    try:
        _step(job_id, "prompt", "active")
        built = build_sheet_prompt(refs, notes, prior_bible)
        reviewed = review_sheet_prompt(built["bible"], built["prompt"], refs)
        bible, base_prompt = reviewed["bible"], reviewed["prompt"]
        _update(job_id, lambda j: j.update(bible=bible, prompt=base_prompt, review_fixes=reviewed["fixes"]))
        _step(job_id, "prompt", "done")

        _step(job_id, "generate", "active")
        image = seedream(base_prompt, refs, SHEET_SIZE, "sheet")
        _step(job_id, "generate", "done")
        attempt = {"number": 1, "mode": "generate", "changes": [], "prompt": base_prompt,
                   "image": image["file"], "image_url": image["url"], "path": image["path"]}
        with _lock:
            _inputs[job_id] = {"refs": refs, "bible": bible, "prompt": base_prompt, "attempts": [attempt]}
        _update(job_id, lambda j: j["attempts"].append({k: v for k, v in attempt.items() if k != "path"}))
        _step(job_id, "done", "active")
        _finish(job_id, attempt)
    except Exception as e:
        _fail(job_id, e)


def _run_inspect(job_id: str) -> None:
    try:
        with _lock:
            inputs = _inputs[job_id]
        refs, bible, base_prompt = inputs["refs"], inputs["bible"], inputs["prompt"]
        latest = inputs["attempts"][-1]
        image = {"file": latest["image"], "url": latest["image_url"], "path": latest["path"]}

        _step(job_id, "checklist", "active")
        checklist = build_checklist(bible, base_prompt)
        _update(job_id, lambda j: j.update(checklist=checklist, attempts=[], inspected=True))
        _step(job_id, "checklist", "done")

        attempts, prompt, mode, changes = [], latest["prompt"], latest["mode"], latest["changes"]
        for n in range(1, MAX_ATTEMPTS + 1):
            if n > 1:
                _step(job_id, "generate", "active")
                if mode == "edit":
                    image = seedream(prompt, [best["image_url"]], SHEET_SIZE, "sheet")
                else:
                    image = seedream(prompt, refs, SHEET_SIZE, "sheet")
                _step(job_id, "generate", "done")
            _step(job_id, "inspect", "active")
            report = inspect(image, refs, bible, checklist)
            criteria = [c for g in report["groups"] for c in g["criteria"]]
            attempt = {"number": n, "mode": mode, "changes": changes, "prompt": prompt,
                       "image": image["file"], "image_url": image["url"], "path": image["path"],
                       "groups": report["groups"], "summary": report["summary"], "audit": report["audit"],
                       "passed": all(g["passed"] for g in report["groups"]),
                       "criteria_total": len(criteria), "criteria_passed": sum(c["passed"] for c in criteria)}
            attempts.append(attempt)
            _update(job_id, lambda j: j["attempts"].append({k: v for k, v in attempt.items() if k != "path"}))
            _step(job_id, "inspect", "done")
            if attempt["passed"] or n == MAX_ATTEMPTS:
                break
            # The single retry: edit the best sheet so far, or regenerate when the layout/ratio is wrong.
            _step(job_id, "retry", "active")
            best = max(attempts, key=_score)
            failed_ids = {g["id"] for g in best["groups"] if not g["passed"]}
            failed, passed = _failure_text(best)
            if failed_ids & {"LAYOUT", "RATIO"}:
                data = llm_json(P.REFINE_SYSTEM, P.REFINE_USER.format(bible=_dump(bible), base_prompt=base_prompt,
                                                                       failed=failed, passed=passed), temperature=0.5,
                                max_tokens=TOKENS["edit"])
                corrections = str(data.get("correction_text") or "").strip()
                prompt, mode = (corrections + "\n\n" + base_prompt) if corrections else base_prompt, "generate"
                changes = ["Full regeneration: the sheet layout or aspect ratio was wrong"] + [str(c) for c in data.get("changes") or []]
            else:
                measured = "\n".join(f"- {d['detail']}: {d['facts']}" for d in best["audit"].get("details") or []) or "(none measured)"
                data = llm_json(P.EDIT_SYSTEM, P.EDIT_USER.format(bible=_dump(bible), failed=failed, passed=passed,
                                                                   measured=measured), temperature=0.4,
                                max_tokens=TOKENS["edit"])
                prompt = str(data.get("edit_instruction") or "").strip()
                if not prompt:
                    raise RuntimeError("Seed LLM returned no edit instruction.")
                mode, changes = "edit", [f"Edited attempt {best['number']}"] + [str(c) for c in data.get("changes") or []]
            _step(job_id, "retry", "done")

        _step(job_id, "done", "active")
        with _lock:
            inputs["attempts"] = attempts
        _finish(job_id, max(attempts, key=_score))
    except Exception as e:
        _fail(job_id, e)
