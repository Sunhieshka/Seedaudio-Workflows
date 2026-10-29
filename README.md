# Short Drama Pipeline

A guided, single-page workflow for short-drama production, built on BytePlus ModelArk: Seed LLM, Seedream, Seedance and Seed Audio 1.5.

| Step | What happens | Code |
|---|---|---|
| 1 · Script to Prompt | Script (paste or PDF) → one Seedance prompt per shot (≤30 s), a character sheet description per character, a location direction per location | `services/script_to_prompt.py` |
| 2 · Character Sheets | 16:9 four-view sheet per character from its description + optional photos; optional QA check & retry | `services/character_sheet_qa.py` |
| 3 · Location Sheets | Empty location plate + N coverage / time-of-day variations edited from the same plate | `services/film_tools.py` (Location Variations) |
| 4 · Asset Library | Chosen sheets/plates (or any uploaded image) go into the project's ModelArk asset group | `services/assets.py` |
| 5 · Shots | Each shot prompt → optimized with the Seedance 2.5 skill → one Seedance video (2.5 by default); regenerating replaces it. Map library references per shot; each becomes `@ImageN is NAME` above the prompt. Optional chaining from the previous shot's last frame | `services/video.py` |
| 6 · Combine | Preview of the full cut (every included shot joined with ffmpeg); unticking a shot also leaves it out of the redub | `services/long_duration.py` |
| 7 · Seed Audio Redub | The episode split into parts of ≤ 3 min; each part's shots combined and redubbed by Seed Audio 1.5 with its own prompt (CINEMATOGRAPHY removed, one timeline), then all parts joined into the final video | `pipeline/audio_prompt.py` |

## UI

A fixed left sidebar (Projects + the seven pipeline tools), a project grid, and a project page where every step is a collapsible tool card stacked in order, each with a status chip (e.g. `2/3 shots ready`). Clicking a tool in the sidebar or the pipeline grid opens and scrolls to its card.

## Redub: prompt and parts

**Prompt.** For each part the server builds: the redub pre-prompt (`[language]` filled in), one `NAME’s voice must match @AudioN.` line per mapped voice, then each shot's Script to Prompt prompt (`audio_prompt.build_prompt`):

- **CINEMATOGRAPHY is always removed** — lens, grade and camera moves are never heard. `IN THIS SHOT`, the timed beats with dialogue, and the AUDIO block (ambience, foley, music) stay.
- **One timeline.** Each shot's local beat times are moved into the part's video time — Shot B starting at 30.0s turns its “2–7s” into “30.0–37.0s”… and each shot is headed `Shot B (30.0s–55.1s):`. A batch of shots then reads as one continuous timeline instead of many restarting at 0. Quoted dialogue is never touched; “N second total runtime” lines are dropped.
- **Seed Audio 1.5 skill** (per part): *when over 10,000 chars* (default), *always*, or *never*. It rewrites the part's prompt into one sound script — lines verbatim with `@音频N`, sounds timed — under the limit; a second pass shortens results still over 10,000 and a repair pass restores any line not word for word. Tested: 13 shots 44,048 → 9,215 chars, no lines lost.

**Parts strategy** (`planParts` in `static/app.jsx`, `audio_prompt.run_part`):

1. **Plan.** The included shots are packed in order into parts of **≤ 3 minutes** and ≤ ~30,000 characters of shot text (what the skill can fit under 10,000). Parts break only at shot boundaries. When a part has to close, it closes at the last **location change** in its back half if there is one, so the seam falls on a scene cut where ambience changes anyway (The Glass Ledger: A–F | G–J, at the move from Hugo's office to the archive). A short episode is one part. ✂ splits a part after any shot; *Merge with part N* joins neighbours.
2. **Each part** (one server job): combine its shots → build its prompt with the part's own timeline from 0 → optimize per the setting → upload the part video (presigned) → Seed Audio redub with the same voices → swap the audio in. *Prepare prompt* stops after the prompt so it can be read or edited; *Redub part* sends the edited prompt as is.
3. **Redub all remaining parts** starts every part; Seed Audio runs two at a time and the rest queue.
4. **Join** concatenates the redubbed parts in order into the final episode (picture untouched; each part's audio is padded/trimmed to its video, so nothing drifts).

Why 3 minutes rather than Seed Audio's 6: a shorter take keeps lip sync and timing tight, keeps each prompt comfortably under 10,000 characters, and a bad take costs one part, not the episode. Keep voices identical across parts by mapping a voice to every speaking character — the card warns when a character on screen has none.

**Voices**: the **Preset voices** gallery plays each preset's sample and can **Assign to…** a character; per character: keep original, a preset (`speaker://<id>`), any Seed Audio voice ID, or an uploaded 2–30 s clip; max 6. “both characters’” becomes “all characters’” when the cast isn't exactly two.

## Tools

Standalone tools in the sidebar, outside any project. Each tool's form and results are saved in `outputs/tools.json`; long work runs as a background job and every failure shows Seed Audio's full error message (with its request id) plus a hint for the common ones.

| Tool | What it does | Code |
|---|---|---|
| **Video Dubbing** | Any video (upload or public URL, < 6 min) + a prompt with every line to be spoken (and any music/ambience/effects) + optional reference voices per speaker (preset gallery, voice ID or clip) → Seed Audio 1.5 `reference_video` dubbing → the video with the new audio. *Insert redub template* fills the pre-prompt and voice lines; optional Seed Audio 1.5 skill optimization first. | `tools.run_dub` |
| **Video Translation** | A video (< 6 min) + target language (30 languages) + optional glossary → Seed Audio 1.5 `dubbing_video` translation (no prompt) → the video with the translated audio. | `tools.run_translate` |
| **Audiobook** | A document (PDF, DOCX, EPUB, TXT, Markdown or pasted text) → Seed LLM splits it into segments, casts narrator and characters, labels every line → Seed Audio voices each segment (preset or sampled voices locked across the book) → ffmpeg assembles an MP3 and an M4B with chapters. | `content_creator/services/audiobook.py`, `static/audiobook.jsx` |

## Skills

| Skill | File | Used by |
|---|---|---|
| Film Script to Seedance | `skills/film-script-to-seedance/SKILL.md` | Script to Prompt (shot prompt format, timing, dialogue rules); loaded by `content_creator/services/script_to_prompt.py` |
| Seedance 2.5 prompt optimizer (sd25-pe) | `skills/sd25-pe/SKILL.md` | Shots: on Generate, each Script to Prompt shot prompt plus its mapped reference images goes to Seed LLM with this skill (`steps.optimize_shot_prompt`), and the result is what Seedance receives. Toggle: “Optimize with SD 2.5 skill” (on by default). The redub uses Script to Prompt's original shot prompts, not the optimized ones. |
| Seed Audio 1.5 prompt optimizer (v1.3.0) | `skills/seedaudio-prompt-optimizer/` | Redub → Optimize (`pipeline/audio_prompt.py`) |

## Run

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/python app.py          # http://127.0.0.1:8100
```

Credentials come from `./.env` (startup prints whether it loaded, or a warning) (see `.env.example`: `ARK_API_KEY`, `BYTEPLUS_AK`, `BYTEPLUS_SK`, `TOS_BUCKET_NAME`, `TOS_REGION`, `ARK_PROJECT`, `SEED_AUDIO_API_KEY`). ffmpeg must be on `PATH`. `PIPELINE_PORT` / `PIPELINE_HOST` change the address.

## Layout

```text
app.py                 entry point (loads .env, starts the server)
pipeline/server.py     HTTP server: page, /files/ (with Range), JSON routes
pipeline/steps.py      the seven steps, composed from content_creator services
pipeline/jobs.py       in-memory background jobs (script, locations, redub)
pipeline/store.py      projects saved whole in outputs/projects.json
content_creator/       ModelArk services: script breakdown, character sheets, Seedream, Seedance, assets, ffmpeg, Seed Audio
static/index.html      page shell + styles
static/app.jsx         the React UI (in-browser Babel, no build step)
static/audiobook.jsx   the Audiobook tool UI (+ static/audiobook.css)
pipeline/tools.py      Video Dubbing, Video Translation and Audiobook routes; saved tool state
outputs/               every generated file (ModelArk links expire after 24 h, so results are kept here)
```

Notes:
- Projects autosave. Running jobs are polled every 4 s; a server restart ends running script/location/redub/sheet jobs (Seedance tasks keep running and are picked up again).
- **Auto-map** (`steps.auto_map_shots`): Seed LLM reads each shot's whole prompt with the library's context (each character's role and look, each location's scenes and description) and attaches the characters on screen — under any name, alias or relationship (“Ethan's grandma” → Agnes) — and the location the shot plays in, including parts of it (“farmhouse doorstep”, “grassy field” on the grounds), whatever the lighting. Possessives don't put someone on screen, and a sheet isn't attached to a character shown at a clearly different age. Reasoning is off for this call (with it on, the model could spend its whole budget thinking and return nothing); three shots per call, in parallel — a 13-shot episode maps in ~4 s. Word matching on `IN THIS SHOT:` (`steps.auto_map`) is the fallback if the call fails.
- **Errors log** (`outputs/errors.log`, `content_creator/utils/errorlog.py`): every failure, one JSON line each — Seed Audio calls (full message, request id, inputs sent, prompt length), Seedance tasks (task id, code; logged once), character-sheet / location / script / redub / tool / audiobook jobs (job id, stage, project, part, shots, traceback) and every API error (route, HTTP status, project). Earlier Seed Audio failures are backfilled, marked `"backfilled": true`.
- Only library items with an asset ID can be mapped to shots.
- Uploads to TOS return a **presigned** URL (7 days) for CreateAsset and Seed Audio, so a private bucket works; the app keeps only asset IDs and local copies afterwards.
- **Upload all to asset library** (Character Sheets, Location Sheets, Asset Library) adds every generated sheet and location image not yet in the library and retries failed uploads. When a shot is chained and has references, the last frame is sent as `@Image1` (Seedance doesn't take a first frame together with reference images), otherwise as the first frame.
- Seed Audio reads public URLs only, so the combined video is uploaded to the TOS bucket before the redub.
