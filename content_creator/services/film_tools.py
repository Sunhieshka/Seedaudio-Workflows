"""Pre-production tools ported from FilmAgent: Previz, Character Masking, Location
Variations.

Every step is one explicit call from the page; nothing chains on its own.
- Reasoning goes to the selected Seed LLM through ModelArk's /responses endpoint
  (it accepts images and video).
- Images go through the Seedream studio's generate(), so results land in OUTPUT_DIR
  with a permanent /files/ URL.
- Prompts are the FilmAgent templates, kept close to verbatim; the user's own words
  are always injected unchanged.
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request

from content_creator.config.settings import OUTPUT_DIR
from content_creator.services import seedream_studio
from content_creator.services.model_settings import prompt_model
from content_creator.utils.llm_images import fit_for_llm

REASON_TIMEOUT_S = 300
# One animatic is one Seedance 2.5 render; the plan's running time is held to this.
ANIMATIC_MAX_SECONDS = 15
# 16:9 frames (schematic, keyframes); edits pass "2K" so the source keeps its shape.
WIDE_SIZE = "2560x1440"
BLOCKOUT_COLORS = ["BLUE", "GREEN", "YELLOW", "RED", "PURPLE", "ORANGE"]

# ---- prompts (FilmAgent promptTemplates.js) -------------------------------------------

MASK_PROMPT = (
    "Reproduce [Image 1] EXACTLY — the same set, camera, framing, lighting and composition — but replace "
    "{targets} with FLAT solid-color silhouettes, one per subject: hard edges, completely filled with one color, "
    "no facial features, no clothing detail, no shading. Assign the colors left to right: blue, then green, then "
    "yellow, then red, then purple (repeat the sequence if there are more figures). Each silhouette keeps its "
    "subject's exact position, scale and pose. Everything NOT replaced stays photorealistic and identical to "
    "[Image 1]. No text or watermarks."
)

PREVIZ_PLAN_SYSTEM = """You are a director and 1st AD BLOCKING one scene for an animatic. Decide where everything is, who moves where, and how the cameras cover it. Never decide final look.

Fix the scene first: the ONE place it happens and the set pieces that define it (doors, tables, windows, vehicles, walls), each with where it sits in the space. Then every actor the description actually names (a crowd collapses to one group, at most 6), and where each one STARTS, bound to a named set piece.

Then the shots, in cut order. For each shot:
  - camera.from: where the camera stands in the space, bound to a set piece ("by the window, looking toward the door");
  - camera.framing: the shot size;
  - camera.move: static, or the move ("slow push in", "pans left with her");
  - action: who moves where during this shot, from a named place to a named place, in plain words ("Mara walks from the door to the table and sits; Tom turns to watch her"). Name actors by the names you gave them;
  - seconds: how long the shot runs, a whole number.
Cover like a director: open wide enough to read the geography, keep every camera on one side of the line of action, go tight only where the story turns. The whole cut runs at most {max_seconds} seconds.

Return ONLY JSON — no prose, no code fences:
{{"scene":"<2 sentences: the space and its features>","axis":"<one sentence: the line of action between the principal actors, against two set pieces>","set":[{{"name":"<short name>","where":"<where it sits in the space>"}}],"actors":[{{"name":"<short name>","description":"<what this actor IS, its kind first, in the description's own words>","start":"<where it starts, bound to a set piece>"}}],"shots":[{{"camera":{{"from":"...","framing":"...","move":"..."}},"action":"...","seconds":4}}]}}"""

PREVIZ_SCHEMATIC = (
    "A TOP-DOWN ORTHOGRAPHIC FLOOR-PLAN SCHEMATIC of a film scene: a clean technical blocking diagram on white, "
    "seen straight down, no perspective. Thin black linework for walls, doors, furniture and every set piece, each "
    "labelled in small block capitals. Each actor is a solid filled circle in their colour with their name beside it "
    "in small capitals. Each movement is a bold arrow in that actor's colour from where they start to where they end, "
    "numbered in order. Each camera is a small black camera symbol with a light-grey view cone opening toward what it "
    "frames, labelled CAM 1, CAM 2 and so on; a camera move is a dashed black arrow from the camera. The line of action "
    "is one thin dashed red line. No shading, no texture, no people drawn as figures, no photographic detail.\n\n"
)
SCHEMATIC_STYLE_LOCK = (
    "clean top-down orthographic technical floor-plan diagram on white, thin black linework, flat coloured circles "
    "and arrows, small block-capital labels, no perspective, no shading, no photographic texture"
)
ANIMATIC_STYLE = (
    "A hard-surface VFX blockout. Every character is ONE rigid solid-colour block: an upright rectangular box about a "
    "person's height, with no head, no arms, no legs and no face. A block never bends, walks or gestures; it slides "
    "across the floor to move and turns in place to face a new way. The set is untextured grey geometry with hard "
    "edges, no texture, no detail, even flat light."
)
PREVIZ_DESCRIBE_SYSTEM = """You watch a previz ANIMATIC: solid coloured blocks standing in for people, sliding and turning through a grey blockout of a set, cut by the cameras. Write what happens in it as the REAL scene, for a video model that will shoot it with this animatic as its motion reference.

Name every block by the person or thing it stands for, using the colour key, and call the set by the place it stands for. Write, in order: who is where, who moves where, what they do, where the camera stands and how it frames and moves, and every cut ("Cut to: ..."), with rough timing when it helps ("for the first 3 seconds").

Never describe the animatic's look: no blocks or boxes, no block colours, no sliding, no grey geometry, untextured surfaces or floor-plan marks. Each block stands for a person who walks, turns and acts naturally — write the person, not the block. Never decide light, grade or wardrobe — the look is set elsewhere. Write only what the video shows; do not add events.

Return ONLY JSON — no prose, no code fences:
{"prompt":"<the scene, plain prose, at most 160 words>"}"""
PREVIZ_CUT_REF = (
    "@Video1 is used for the blocking, the timing of the action, the camera positions and moves, and the cuts; do not "
    "use its look — its solid stand-in blocks, their flat colours and the grey set geometry; the people move naturally, "
    "not as blocks."
)

LOCATION_ANCHOR = "{brief}\n\nOne location, with nothing alive anywhere in the frame. No text, no lettering, no watermark."
LOCATION_PLAN_SYSTEM = (
    "You are a location scout and DP. The attached image is the canonical location — it will be EDITED, not "
    "re-generated: an image-edit model receives it as [Image 1] and keeps EVERYTHING you do not name (architecture, "
    "layout, materials, set dressing; no people appear unless named). Propose {count} DISTINCT variations as EDIT "
    "INSTRUCTIONS. Each \"prompt\" is ONE short instruction naming ONLY what changes: COVERAGE ('Reframe to a low wide "
    "from the opposite end — the same location', 'Reframe to a high aerial — the same location') or STATE ('it is "
    "night, heavy rain, the windows lit warm'). Never re-describe the location — the image carries it. Vary what the "
    "user asks for; else pick the most interesting mix of angles, time of day, weather, season. Return ONLY a JSON "
    "array of {count} objects {{\"label\": 2–5 words, \"prompt\": the edit instruction}}. Substantially different "
    "options. No prose, no code fences."
)
FRAME_EDIT = "EDIT [Image 1], change only: {instruction}"

# ---- ModelArk plumbing ----------------------------------------------------------------

def _post(path: str, body: dict, timeout: int) -> dict:
    key = seedream_studio._api_key()
    if not key:
        raise RuntimeError("No ARK API key. Set ARK_API_KEY in .env and restart.")
    req = urllib.request.Request(
        f"{seedream_studio._base_url()}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            err = json.loads(raw).get("error") or {}
            msg = err.get("message") if isinstance(err, dict) else str(err)
        except ValueError:
            msg = raw[:600]
        raise RuntimeError(msg or f"HTTP {e.code}") from None


def _video_data_url(ref: str) -> str:
    if ref.startswith("/files/"):
        name = os.path.basename(ref[len("/files/"):].split("?")[0])
        path = os.path.join(OUTPUT_DIR, name)
        if not os.path.isfile(path):
            raise ValueError(f"Video not found on the server: {name}")
        with open(path, "rb") as f:
            return "data:video/mp4;base64," + base64.b64encode(f.read()).decode("ascii")
    return ref


def reason(system: str, user: str, images: list | None = None, video: str = "") -> str:
    """One Seed LLM call; returns the output text."""
    content: list[dict] = [{"type": "input_text", "text": user}]
    for ref in images or []:
        content.append({"type": "input_image", "image_url": fit_for_llm(seedream_studio._resolve(ref))})
    if video:
        content.append({"type": "input_video", "video_url": _video_data_url(video)})
    data = _post("/responses", {
        "model": prompt_model(),
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": system}]},
            {"role": "user", "content": content},
        ],
    }, REASON_TIMEOUT_S)
    chunks = []
    for item in data.get("output") or []:
        if item.get("type") != "message":
            continue
        for c in item.get("content") or []:
            if c.get("type") == "output_text":
                chunks.append(c.get("text") or "")
    return "".join(chunks).strip()


def parse_json(text: str):
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    try:
        return json.loads(t)
    except ValueError:
        pass
    for open_c, close_c in (("{", "}"), ("[", "]")):
        a, b = t.find(open_c), t.rfind(close_c)
        if a != -1 and b > a:
            try:
                return json.loads(t[a:b + 1])
            except ValueError:
                continue
    return None


def image(prompt: str, refs: list | None = None, size: str = "2K", model: str = "pro") -> str:
    """One Seedream render; returns its /files/ URL."""
    body = {"model": model, "prompt": prompt, "size": size, "output_format": "png", "watermark": False}
    refs = [r for r in (refs or []) if r]
    if refs:
        body["image"] = refs if len(refs) > 1 else refs[0]
    status, data = seedream_studio.generate(body)
    if not (200 <= status < 300):
        raise RuntimeError((data.get("error") or {}).get("message") or f"Seedream HTTP {status}")
    first = (data.get("data") or [{}])[0]
    url = first.get("local_url") or first.get("url")
    if not url:
        raise RuntimeError("The image model returned no image.")
    return url


def _clean(v, n: int) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:n]


# ---- Character Masking ------------------------------------------------------------------

def mask(image_url: str, targets: str = "", size: str = "2K") -> dict:
    if not image_url:
        raise ValueError("Upload an image to mask.")
    who = _clean(targets, 1000) or "EVERY person in the frame"
    prompt = MASK_PROMPT.replace("{targets}", who)
    return {"url": image(prompt, [image_url], size), "prompt": prompt}


# ---- Previz -----------------------------------------------------------------------------

def previz_plan(brief: str, camera: str = "", max_seconds: int = ANIMATIC_MAX_SECONDS) -> dict:
    text = (brief or "").strip()
    if not text:
        raise ValueError("Describe the scene first.")
    user = (f'SCENE DESCRIPTION (verbatim):\n"""\n{text[:6000]}\n"""\n'
            f"CAMERA PREFERENCE: {camera.strip() or 'the planner chooses'}\n\nBlock the scene and return the JSON.")
    raw = parse_json(reason(PREVIZ_PLAN_SYSTEM.format(max_seconds=max_seconds), user)) or {}
    actors = [{"name": _clean(a.get("name"), 40), "description": _clean(a.get("description"), 200), "start": _clean(a.get("start"), 160)}
              for a in (raw.get("actors") or []) if isinstance(a, dict)]
    actors = [a for a in actors if a["name"]][:len(BLOCKOUT_COLORS)]
    for i, a in enumerate(actors):
        a["color"] = BLOCKOUT_COLORS[i]
    set_pieces = [{"name": _clean(s.get("name"), 40), "where": _clean(s.get("where"), 160)}
                  for s in (raw.get("set") or []) if isinstance(s, dict) and s.get("name")]
    shots = []
    for s in raw.get("shots") or []:
        if not isinstance(s, dict) or not s.get("action"):
            continue
        cam = s.get("camera") or {}
        shots.append({
            "camera": {"from": _clean(cam.get("from"), 160), "framing": _clean(cam.get("framing"), 80), "move": _clean(cam.get("move"), 120)},
            "action": _clean(s.get("action"), 600),
            "seconds": max(1, int(round(float(s.get("seconds") or 3)))),
        })
    if not shots:
        raise RuntimeError("The plan came back with no shots — try blocking again.")
    # Hold the cut to one animatic: trim shot lengths proportionally if it runs over.
    total = sum(s["seconds"] for s in shots)
    if total > max_seconds:
        scale = max_seconds / total
        for s in shots:
            s["seconds"] = max(1, int(s["seconds"] * scale))
    return {"scene": _clean(raw.get("scene"), 1200), "axis": _clean(raw.get("axis"), 300), "set": set_pieces, "actors": actors, "shots": shots}


def _camera_line(s: dict, i: int) -> str:
    cam = s.get("camera") or {}
    return ", ".join(x for x in [f"CAM {i + 1}", cam.get("from"), cam.get("framing"), cam.get("move")] if x)


def previz_schematic(plan: dict) -> dict:
    set_pieces, actors, shots = plan.get("set") or [], plan.get("actors") or [], plan.get("shots") or []
    if not set_pieces and not actors:
        raise ValueError("Plan the scene first.")
    lines = [
        ("The set: " + "; ".join(f"{s['name'].upper()}" + (f" ({s['where']})" if s.get("where") else "") for s in set_pieces) + ".") if set_pieces else (f"The set: {plan.get('scene')}" if plan.get("scene") else ""),
        ("The actors: " + "; ".join(f"{a['name']} — a solid {a.get('color', '').lower()} circle" + (f", starting {a['start']}" if a.get("start") else "") for a in actors) + ".") if actors else "",
        ("The movements, numbered in order: " + " ".join(f"{i + 1}. {s['action']}" for i, s in enumerate(shots))) if shots else "Nobody moves: no arrows are drawn.",
        ("The cameras: " + "; ".join(_camera_line(s, i) for i, s in enumerate(shots)) + ".") if any((s.get("camera") or {}).get("from") for s in shots) else "No cameras are drawn.",
        f"The line of action: {plan['axis']}." if plan.get("axis") else "",
    ]
    prompt = PREVIZ_SCHEMATIC + "\n".join(x for x in lines if x)
    return {"url": image(prompt, None, WIDE_SIZE), "prompt": prompt}


def previz_schematic_edit(url: str, instruction: str) -> dict:
    if not url or not (instruction or "").strip():
        raise ValueError("Say what to change on the schematic.")
    prompt = FRAME_EDIT.replace("{instruction}", f"{instruction.strip()[:1500]}. Keep it a {SCHEMATIC_STYLE_LOCK}.")
    return {"url": image(prompt, [url]), "prompt": prompt}


def previz_animatic_prompt(plan: dict) -> str:
    actors, shots = plan.get("actors") or [], plan.get("shots") or []
    key = ("The colour key: " + "; ".join(f"the {a.get('color', '').lower()} block is {a['name']}" for a in actors) + ".") if actors else ""
    shot_lines = "\n".join(f"Shot {i + 1} ({_camera_line(s, i)}): {s['action']}" for i, s in enumerate(shots))
    return (f"An ANIMATIC of one scene. {ANIMATIC_STYLE}\n@Image1 is a top-down floor plan of this scene: use only its layout, "
            f"where each actor starts and the paths the arrows show; the shots below decide the cameras. Never show the plan "
            f"itself — no lines, labels, circles or arrows on screen.\n{key} Each block keeps its colour for the whole video; "
            f"only colour tells them apart.\n{shot_lines}").strip()


def previz_cut_prompt(video_url: str, plan: dict) -> str:
    if not video_url:
        raise ValueError("Make an animatic first.")
    cast = "\n".join(f"- the {a.get('color', '').lower()} block = {a['name']}" + (f": {a['description']}" if a.get("description") else "")
                     for a in plan.get("actors") or [])
    seconds = sum(int(s.get("seconds") or 0) for s in plan.get("shots") or [])
    user = (f"THE SCENE: {plan.get('scene') or '(not stated)'}\n"
            f"THE SET: {'; '.join(s['name'] + (' (' + s['where'] + ')' if s.get('where') else '') for s in plan.get('set') or []) or '(not stated)'}\n"
            f"THE COLOUR KEY (who each block stands for):\n{cast or '(no named figures)'}\nLENGTH: {seconds or 'unknown'} seconds\n\n"
            "Watch the attached animatic and return the JSON.")
    text = str((parse_json(reason(PREVIZ_DESCRIBE_SYSTEM, user, video=video_url)) or {}).get("prompt") or "").strip()
    if not text:
        raise RuntimeError("The animatic description came back empty — try again.")
    return PREVIZ_CUT_REF + "\n" + text


# ---- Location Variations ----------------------------------------------------------------

def location_plan(image_url: str = "", direction: str = "", count: int = 4, size: str = "2K") -> dict:
    count = max(1, min(8, int(count or 4)))
    source, anchor = image_url, False
    if not source:
        brief = (direction or "").strip()
        if not brief:
            raise ValueError("Upload a location image, or describe the location.")
        source, anchor = image(LOCATION_ANCHOR.replace("{brief}", brief), None, size), True
    user = (f"Concept: (none given)\nWhat to explore: {(direction or '').strip() or '(your call — choose the most interesting dimensions)'}\n\n"
            f"Produce exactly {count} options as the specified JSON array — every option substantially different from the others.")
    items = []
    for _ in range(2):
        parsed = parse_json(reason(LOCATION_PLAN_SYSTEM.format(count=count), user, images=[source]))
        if isinstance(parsed, dict):
            parsed = parsed.get("options") or parsed.get("variations") or []
        items = [{"label": _clean(x.get("label"), 40), "prompt": _clean(x.get("prompt"), 600)}
                 for x in (parsed or []) if isinstance(x, dict) and x.get("prompt")]
        if items:
            break
    if not items:
        raise RuntimeError("The planner returned no variations — try again.")
    return {"source": source, "anchor": anchor, "items": items[:count]}


def location_render(source: str, instruction: str, size: str = "2K") -> dict:
    if not source or not (instruction or "").strip():
        raise ValueError("A source image and an instruction are required.")
    prompt = FRAME_EDIT.replace("{instruction}", instruction.strip()[:2000])
    return {"url": image(prompt, [source], size), "prompt": prompt}
