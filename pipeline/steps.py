"""The workflow steps, built on the content_creator ModelArk services.

1. Script -> shot prompts, character sheet descriptions, location directions
2. Character sheets (Character Sheet QA pipeline, text and/or reference images)
3. Location sheets (an empty plate plus optional coverage variations)
4. Asset library (ModelArk asset group, so references can be bound as asset://)
5. Shots (Seedance, references mapped from the library) and combine (ffmpeg)
6. Redub the combined video with Seed Audio, voices mapped to characters
"""

import base64
import json
import mimetypes
import os
import re
import time
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

from content_creator.config.settings import OUTPUT_DIR
from content_creator.services import film_tools, seed_audio
from content_creator.services.assets import (
    create_asset_from_data_url,
    create_group_and_asset_from_data_url,
    create_asset_group,
    load_asset_env,
    normalize_asset_url,
)
from content_creator.services.long_duration import combine_videos_ffmpeg
from content_creator.services.script_to_prompt import (
    extract_text_from_pdf_bytes,
    generate_character_sheet_prompts,
    generate_location_prompts,
    generate_seedance_prompts_from_script,
)
from content_creator.services.video import VIDEO_MODELS, _video_prompt_system_instruction, create_video_task, get_video_task
from content_creator.services.characters import _extract_output_text
from content_creator.services.model_settings import prompt_model
from content_creator.utils.llm_images import fit_all_for_llm
from byteplussdkarkruntime import Ark

DOWNLOAD_TIMEOUT_S = 300


# ---- local files ---------------------------------------------------------------

def local_path(url: str) -> str:
    """The outputs/ path behind a /files/ URL (or "")."""
    if not str(url or "").startswith("/files/"):
        return ""
    name = os.path.basename(url[len("/files/"):].split("?")[0])
    path = os.path.join(OUTPUT_DIR, name)
    return path if name and os.path.isfile(path) else ""


def data_url_of(url: str) -> str:
    """A /files/ URL as a data URL; data and http(s) URLs pass through."""
    if url.startswith(("data:", "http://", "https://", "asset://")):
        return url
    path = local_path(url)
    if not path:
        raise ValueError(f"File not found on the server: {url}")
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    with open(path, "rb") as f:
        return f"data:{mime};base64," + base64.b64encode(f.read()).decode("ascii")


def save_upload(data_url: str, hint: str) -> str:
    """Stores an uploaded data: URL file in outputs/ and returns its /files/ URL."""
    header, _, b64 = data_url.partition(",")
    if not header.startswith("data:") or not b64:
        raise ValueError("Expected a base64 data: URL")
    mime = header[5:].split(";", 1)[0].lower()
    ext = mimetypes.guess_extension(mime) or ".bin"
    ext = {".jpe": ".jpg", ".jpeg": ".jpg"}.get(ext, ext)
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in hint)[:40].strip("-") or "upload"
    name = f"upload-{safe}-{int(time.time() * 1000)}{ext}"
    with open(os.path.join(OUTPUT_DIR, name), "wb") as f:
        f.write(base64.b64decode(b64))
    return "/files/" + name


def _keep(url: str, name: str) -> str:
    """Downloads a remote result into outputs/ once (ModelArk links expire in 24 h)."""
    path = os.path.join(OUTPUT_DIR, name)
    if not os.path.isfile(path):
        # A unique temp file per download: two status polls for the same shot (two open tabs) used to share
        # one ".part" file, and the second rename failed after the first had moved it.
        tmp = f"{path}.{uuid.uuid4().hex[:8]}.part"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"})
        try:
            with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT_S) as resp, open(tmp, "wb") as f:
                f.write(resp.read())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    return "/files/" + name


# ---- 1. script -> prompts ------------------------------------------------------

_SHOT_HEADING = re.compile(r"^SHOT\s+(\d+)\s*$", re.MULTILINE)
_RUNTIME = re.compile(r"(\d+)\s*second total runtime", re.IGNORECASE)


def split_shots(text: str) -> list:
    """Splits Script to Prompt's "SHOT N" output into one entry per shot."""
    parts = _SHOT_HEADING.split(text or "")
    shots = []
    # parts = [preamble, n1, body1, n2, body2, ...]
    for i in range(1, len(parts) - 1, 2):
        body = parts[i + 1].strip()
        if not body:
            continue
        m = _RUNTIME.search(body)
        shots.append({"number": int(parts[i]), "prompt": body, "seconds": int(m.group(1)) if m else 15})
    if not shots and (text or "").strip():
        shots.append({"number": 1, "prompt": text.strip(), "seconds": 15})
    return shots


def script_to_prompts(script_text: str, pdf_data_url: str, progress) -> dict:
    parts = [script_text.strip()]
    if pdf_data_url:
        progress("Reading PDF")
        _, _, b64 = pdf_data_url.partition(",")
        parts.append(extract_text_from_pdf_bytes(base64.b64decode(b64)))
    script = "\n\n".join(p for p in parts if p.strip())
    if not script:
        raise ValueError("Paste a script or upload a PDF.")
    progress("Writing shot prompts, character sheets and locations")
    # The three lists come from one script, in parallel.
    with ThreadPoolExecutor(max_workers=3) as pool:
        video_f = pool.submit(generate_seedance_prompts_from_script, script)
        chars_f = pool.submit(generate_character_sheet_prompts, script)
        locs_f = pool.submit(generate_location_prompts, script)
        shots = split_shots(video_f.result())
        try:
            characters = chars_f.result()
        except Exception:
            characters = []
        try:
            locations = locs_f.result()
        except Exception:
            locations = []
    return {"script": script, "shots": shots, "characters": characters, "locations": locations}


# ---- 3. location sheets --------------------------------------------------------

def location_sheet(direction: str, variations: int, progress) -> dict:
    """An empty plate of the place, then N coverage/state edits of that same plate."""
    if not direction.strip():
        raise ValueError("Write a location direction first.")
    # The direction's "Explore:" part lists the variations; the plate only needs the place.
    place, _, explore = direction.partition("Explore:")
    progress("Rendering location plate")
    plate = film_tools.image(film_tools.LOCATION_ANCHOR.replace("{brief}", place.strip() or direction.strip()),
                             None, film_tools.WIDE_SIZE)
    out = {"plate": plate, "variations": []}
    if variations > 0:
        progress("Planning variations")
        plan = film_tools.location_plan(plate, explore.strip() or direction.strip(), variations)
        progress(f"Rendering {len(plan['items'])} variations")
        with ThreadPoolExecutor(max_workers=4) as pool:
            renders = list(pool.map(lambda it: film_tools.location_render(plate, it["prompt"]), plan["items"]))
        out["variations"] = [{"label": it["label"], "prompt": it["prompt"], "url": r["url"]}
                             for it, r in zip(plan["items"], renders)]
    return out


# ---- 4. asset library ----------------------------------------------------------

def add_to_library(group_id: str, project_name: str, image_url: str, name: str) -> dict:
    """Uploads one image to the project's ModelArk asset group (made on first use)."""
    if not group_id:
        env = load_asset_env()
        group_id = create_asset_group(name=(project_name or "Short drama")[:60],
                                      description=f"Short drama asset library: {project_name}"[:200],
                                      region=env.region, project_name=env.project_name)
    hint = "".join(c if c.isalnum() else "-" for c in (name or "ref").lower())[:30] or "ref"
    asset = create_asset_from_data_url(data_url=data_url_of(image_url), group_id=group_id,
                                       key_hint=hint, asset_type="Image")
    return {"group_id": group_id, "asset_id": asset["asset_id"]}


# ---- 5. shots ------------------------------------------------------------------

def _is_asset_processing(message: str) -> bool:
    m = (message or "").lower()
    return "asset is still processing" in m or "still processing and is not available yet" in m


_TITLES = {"detective", "det", "dr", "mr", "mrs", "ms", "miss", "sir", "officer", "inspector", "captain", "the"}
_PLACE_NOISE = {"int", "ext", "interior", "exterior", "night", "day", "dawn", "dusk", "continuous", "morning", "evening",
                "the", "and", "locations", "location", "key", "props", "later"}


def _tokens(text: str, drop: set) -> list:
    text = re.sub(r"[’']s\b", "", text.lower())  # "hugo thun's office" -> "hugo thun office"
    return [w for w in re.findall(r"[a-z0-9]+", text) if w not in drop and len(w) > 1]


def auto_map(prompt: str, items: list) -> list:
    """Library item ids for the characters and locations named in a shot's "IN THIS SHOT:" line.

    The line is a list of entries ("Ingrid Falkenrath, Detective Marc Oyelaran; Annelise Voss's apartment").
    An entry is a character only when all its words belong to that character's name (so "Annelise Voss's
    apartment" or "Falkenrath & Thun Private Bank" never pull in a person). Otherwise it is matched to the
    location whose name words it covers best (so "Private Bank Atrium" finds "BANK ATRIUM (INTERIOR)").
    One image per character / location; the first library image for it wins (the plate before variations).
    """
    m = re.search(r"IN THIS SHOT:\s*(.+)", prompt or "", re.IGNORECASE)
    if not m:
        return []
    entries = [e for e in re.split(r"[,;]", m.group(1)) if e.strip()]
    chars = [it for it in items if it.get("kind") == "character"]
    places = [it for it in items if it.get("kind") != "character"]
    picked, subjects = [], set()

    def pick(it):
        subj = (it.get("subject") or it.get("name") or "").lower()
        if subj not in subjects:
            subjects.add(subj)
            picked.append(it["id"])

    for entry in entries:
        words = _tokens(entry, _TITLES)
        char = next((c for c in chars if words and set(words) <= set(_tokens(c.get("subject") or c["name"], _TITLES))), None)
        if char:
            pick(char)
            continue
        words = set(_tokens(entry, _PLACE_NOISE))
        best, best_score = None, (0.0, 0)
        for it in places:
            name = set(_tokens(it.get("subject") or it["name"], _PLACE_NOISE))
            hit = len(name & words)
            score = (hit / len(name) if name else 0.0, hit)
            if hit and score[0] >= 0.6 and score > best_score:
                best, best_score = it, score
        if best:
            pick(best)
    return picked


AUTO_MAP_SYSTEM = """You map a film's reference images to its shots. For each shot you get its full video prompt; you also get the reference library: character sheets (with each character's role and look) and location plates (with the scenes they belong to and what the place looks like).

For every shot, list the library items to attach:
- A character item when that character is ON SCREEN in the shot — under any name the shot uses: first name, full name, title, a relationship ("Ethan's grandma" = Agnes when the library says Agnes is Ethan's grandmother), or a description that unambiguously identifies them. A possessive does not put someone on screen ("Annelise Voss's apartment" does not show Annelise). A different person stays different ("the Young Witch's mother" is not the Young Witch). Do not attach a character sheet when the shot shows that character at a clearly different age than the sheet (an elderly grandmother's sheet is not attached to "a middle-aged woman" playing her decades earlier) — a mismatched sheet would age them wrongly.
- A location item when the shot takes place in that place or any part of it — a doorway, doorstep, yard, field or room of it ("farmhouse doorstep", "grassy field on the Gremory grounds" → the Gremory farmhouse & grounds plate). Each location lists the scenes it is used for: a shot that plays one of those scenes gets that location. Time of day, weather and lighting never rule a location out — the plate is a reference for the place, not its light. If the shot cuts between places, include each. Do not attach a place that is only mentioned.
- At most one item per character and per location: prefer the plate over variations unless a variation's label clearly matches the shot (night, rain, a specific angle).
Use only ids from the library. Leave a shot's list empty rather than guess.

Return ONLY JSON — no prose, no code fences:
{"shots":[{"id":"<shot id>","items":["<library id>", ...]}]}"""


def auto_map_shots(shots: list, items: list) -> dict:
    """{shot id: [library item ids]} decided by Seed LLM from the whole shot prompts and the library's
    roles and descriptions (aliases, relationships, parts of a place). Falls back to auto_map per shot."""
    fallback = {str(s.get("id")): auto_map(str(s.get("prompt") or ""), items) for s in shots}
    if not shots or not items:
        return fallback
    lib = "\n".join(f'- id {it["id"]}: {it.get("kind") or "other"} "{it.get("subject") or it["name"]}"'
                    + (f' ({it["label"]})' if it.get("label") else "")
                    + (f' — {str(it.get("context"))[:400]}' if it.get("context") else "") for it in items)
    body = "\n\n".join(f'SHOT id {s.get("id")}:\n"""\n{str(s.get("prompt") or "")[:6000]}\n"""' for s in shots)
    try:
        from content_creator.services.script_to_prompt import _ask
        # Reasoning off: with it on, the model can spend the whole budget thinking and return nothing.
        raw = _ask(AUTO_MAP_SYSTEM, f"REFERENCE LIBRARY:\n{lib}\n\n{body}\n\nReturn the JSON.", temperature=0.1,
                   max_tokens=16000, think=False)
        data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    except Exception:
        return fallback
    valid = {it["id"]: it for it in items}
    out = dict(fallback)
    for row in data.get("shots") or []:
        sid = str((row or {}).get("id") or "")
        if sid not in out:
            continue
        picked, subjects = [], set()
        for iid in row.get("items") or []:
            it = valid.get(str(iid))
            subj = ((it or {}).get("subject") or (it or {}).get("name") or "").lower()
            if it and subj not in subjects:
                subjects.add(subj)
                picked.append(it["id"])
        out[sid] = picked
    return out


def build_shot_prompt(prompt: str, ref_names: list, has_start_frame: bool) -> str:
    """Binds each reference image to the prompt as `@ImageN is NAME`."""
    lines = []
    n = 1
    if has_start_frame:
        lines.append("@Image1 is the last frame of the previous shot: open this shot on it as the first frame "
                     "and continue seamlessly from it.")
        n = 2
    for i, name in enumerate(ref_names):
        lines.append(f"@Image{n + i} is {name}.")
    return ("\n".join(lines) + "\n\n" + prompt.strip()) if lines else prompt.strip()


def optimize_shot_prompt(prompt: str, roles: list, images: list) -> str:
    """Script to Prompt's shot prompt -> Seed LLM with the Seedance 2.5 skill (skills/sd25-pe) -> the prompt
    Seedance gets. Seed LLM sees each reference image with its role ("Image 2: Ingrid Falkenrath"), so the
    rewrite can refer to them; dialogue, beats and runtime come from the shot prompt."""
    role_lines = [f"Image {i + 1}: {r}." for i, r in enumerate(roles)]
    system = _video_prompt_system_instruction(len(roles), role_lines)
    content = [{"type": "input_text", "text": "User content: " + prompt.strip() + "\n\nKeep every spoken line word for word, "
                "with its speaker, and keep the shot's beats in order within its runtime."}]
    for url in fit_all_for_llm([data_url_of(u) for u in images if u]):
        content.append({"type": "input_image", "detail": "high", "image_url": url})

    def ask(extra: str = "") -> str:
        parts = content if not extra else [dict(content[0], text=content[0]["text"] + extra)] + content[1:]
        resp = Ark().responses.create(model=prompt_model(), temperature=0.2,
                                      input=[{"role": "system", "content": system}, {"role": "user", "content": parts}])
        return (_extract_output_text(resp) or "").strip()

    out = ask()
    # The rewrite has dropped or cut lines (pocket 3 shot F lost half a line, so the video never spoke it).
    # Every scripted line must survive word for word: ask once more with the exact lines, and if a line is
    # still missing, send the original shot prompt rather than a video prompt that loses dialogue.
    lost = _lines_missing(prompt, out)
    if lost:
        out = ask("\n\nYour previous rewrite dropped or changed these spoken lines. Include each one exactly as "
                  "written here, every word, with its speaker:\n" + "\n".join(f'- "{l}"' for l in lost))
        if not out or _lines_missing(prompt, out):
            from content_creator.utils import errorlog
            errorlog.log("sd25-optimize", "SD 2.5 rewrite kept dropping dialogue; the original shot prompt was used",
                         lines=_lines_missing(prompt, out) if out else lost)
            return prompt.strip()
    return out or prompt.strip()


def _lines_missing(script: str, rewritten: str) -> list:
    """Quoted spoken lines of the script (two words or more) not found word for word in the rewrite."""
    norm = lambda t: re.sub(r"[\W_]+", " ", t or "").strip().lower()
    have = norm(rewritten)
    lines = dict.fromkeys(l.strip() for l in re.findall(r'["“]([^"”]{2,}?)["”]', script or "") if len(l.split()) > 1)
    return [l for l in lines if norm(l) not in have]


def start_shot(payload: dict) -> dict:
    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("The shot has no prompt.")
    model = str(payload.get("model") or "").strip() or next(iter(VIDEO_MODELS))
    spec = VIDEO_MODELS.get(model)
    if not spec:
        raise ValueError(f"Unknown video model: {model}")
    duration = max(4, min(spec["max_duration"], int(payload.get("duration") or 15)))
    resolution = str(payload.get("resolution") or "720p")
    if resolution not in spec["resolutions"]:
        resolution = spec["resolutions"][-1] if resolution == "1080p" else spec["resolutions"][0]
    takes = max(1, min(3, int(payload.get("takes") or 1)))

    refs = [r for r in payload.get("refs") or [] if isinstance(r, dict) and r.get("asset_id")]
    ref_urls = [normalize_asset_url(r["asset_id"]) for r in refs]
    ref_names = [str(r.get("name") or "a reference").strip() for r in refs]

    # The previous shot's last frame: a first_frame on its own, or @Image1 when there are references
    # (Seedance does not take a first_frame together with reference images).
    start_url = ""
    start_frame = str(payload.get("start_frame") or "").strip()
    if start_frame:
        start_url = start_frame if start_frame.startswith("http") else normalize_asset_url(
            create_group_and_asset_from_data_url(data_url=data_url_of(start_frame), key_hint="start-frame",
                                                 asset_type="Image")["asset_id"])
    as_ref = bool(start_url and ref_urls)
    images = ([start_url] if as_ref else []) + ref_urls
    if len(images) > spec["max_ref_images"]:
        raise ValueError(f"Seedance {spec['label']} takes at most {spec['max_ref_images']} reference images.")
    optimized = ""
    if payload.get("optimize", True):
        # Seed LLM views the library's local copies; Seedance gets the asset:// references.
        roles = (["the last frame of the previous shot: open this shot on it as the first frame"] if as_ref else []) + ref_names
        views = ([start_frame] if as_ref else []) + [str(r.get("image") or "") for r in refs]
        if len([v for v in views if v]) == len(roles):
            optimized = optimize_shot_prompt(prompt, roles, views)
        else:  # no viewable copy for some reference: describe roles only
            optimized = optimize_shot_prompt(prompt, roles, [])
    final_prompt = build_shot_prompt(optimized or prompt, ref_names, as_ref)

    def create() -> str:
        for attempt in range(8):
            try:
                return create_video_task(prompt=final_prompt, reference_images=images,
                                         first_frame_image=(start_url if start_url and not as_ref else None),
                                         ratio=str(payload.get("ratio") or "16:9"), resolution=resolution,
                                         duration=duration, model=model)
            except Exception as e:
                # Newly added assets take a little while before Seedance can read them.
                if not _is_asset_processing(str(e)) or attempt == 7:
                    raise
                time.sleep(min(1.5 * (2 ** attempt), 20))
        return ""

    task_ids = [create() for _ in range(takes)]
    return {"task_ids": [t for t in task_ids if t], "prompt": final_prompt, "optimized": optimized}


def shot_status(task_id: str, context: dict = None) -> dict:
    task = get_video_task(task_id=task_id)
    out = {"task_id": task_id, "status": task["status"], "error": task["error"].get("message") or ""}
    if task["status"] in ("failed", "expired", "cancelled"):
        from content_creator.utils import errorlog
        errorlog.log("seedance", out["error"] or f"Seedance task {task['status']}", once_key=task_id, task_id=task_id,
                     code=task["error"].get("code"), status=task["status"], **(context or {}))
    if task["status"] == "succeeded" and task.get("video_url"):
        out["video"] = _keep(task["video_url"], f"shot-{task_id}.mp4")
        if task.get("last_frame_url"):
            try:
                out["last_frame"] = _keep(task["last_frame_url"], f"shot-{task_id}-last.png")
            except Exception:
                out["last_frame"] = ""
    return out


def probe_duration(path: str) -> float:
    """Seconds of a media file, read from ffmpeg's header dump (ffprobe isn't always installed)."""
    import subprocess
    from content_creator.services.long_duration import get_ffmpeg_path
    p = subprocess.run([get_ffmpeg_path(), "-hide_banner", "-i", path], capture_output=True, text=True)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", p.stderr or "")
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0


def combine(video_urls: list) -> dict:
    """Joins the shots in order; returns the cut, its length and where each shot starts and ends in it."""
    paths = [local_path(u) for u in video_urls]
    if not paths or not all(paths):
        raise ValueError("Every shot must have a finished video before combining.")
    spans, t = [], 0.0
    for path in paths:
        d = probe_duration(path)
        spans.append({"start": round(t, 2), "end": round(t + d, 2)})
        t += d
    if len(paths) == 1:
        url = video_urls[0]
    else:
        # combine_videos_ffmpeg downloads with urllib, which reads file:// URLs too.
        url = "/files/" + os.path.basename(combine_videos_ffmpeg(video_urls=["file://" + p for p in paths], output_dir=OUTPUT_DIR))
    return {"url": url, "duration": round(probe_duration(local_path(url)) or t, 2), "spans": spans}


# ---- 6. redub ------------------------------------------------------------------

def redub(video_url: str, prompt: str, voices: list, output: dict, progress) -> dict:
    """Combined video + pre-prompt + shot prompts -> Seed Audio -> audio swapped into the video."""
    if not prompt.strip():
        raise ValueError("The redub prompt is empty.")
    path = local_path(video_url)
    if path and probe_duration(path) >= 360:
        raise ValueError("Seed Audio takes videos shorter than 6 minutes. Combine fewer shots and redub in parts.")
    source = video_url
    if not source.startswith("http"):
        progress("Uploading combined video (Seed Audio reads public URLs)")
        source = seed_audio.upload_source_video(data_url_of(video_url))

    # Order matters: the prompt calls the voices @Audio1, @Audio2, ... in this order.
    content = [{"type": "text", "text": prompt.strip()}]
    for v in voices:
        content.append({"type": "audio_url", "audio_url": {"url": v}, "role": "reference_audio"})
    content.append({"type": "video_url", "video_url": {"url": source}, "role": "reference_video"})
    body = {"content": content, "output_format": output.get("format") or "wav",
            "audio_config": {"sample_rate": int(output.get("sample_rate") or 44100), "speech_rate": 0,
                             "loudness_rate": 0, "pitch_rate": 0}}

    progress("Seed Audio is redubbing (this can take several minutes)")
    status, data = seed_audio.generate(body, wait_s=15 * 60)
    if not (200 <= status < 300):
        err = data.get("error")
        raise RuntimeError((err.get("message") if isinstance(err, dict) else str(err or "")) or f"Seed Audio HTTP {status}")
    audios = [a for a in ((data.get("content") or {}).get("audios") or []) if isinstance(a, dict)]
    if not audios:
        raise RuntimeError("Seed Audio returned no audio.")
    tracks = [{"type": a.get("type") or "mixed", "url": a.get("local_url") or a.get("url")} for a in audios]
    track = next((t for t in tracks if t["type"] == "mixed"), tracks[0])

    progress("Replacing the video's audio")
    final = seed_audio.replace_video_audio(video_url, track["url"])
    return {"source_url": source, "audio": track["url"], "tracks": tracks, "final": final}
