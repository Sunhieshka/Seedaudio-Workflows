name: film-script-to-seedance description: Convert film scripts into Seedance 2.0 video generation prompts using its reference system. Use this skill whenever a user provides a film script, screenplay excerpt, or scene breakdown and asks to turn it into shot prompts for Seedance, or whenever they mention "shot prompts," "video gen prompts from a script," "Seedance prompts," or attach reference images (characters, locations, props, weapons, last-frame plates) alongside scene text. Also use when iterating on existing Seedance shot prompts — refining shot description, audio design, cinematography, continuity between shots, or extracting last-frame keyframes for chaining. Default to 30-second shots and the structured prompt format described below.

Film Script → Seedance 2.0 Prompts
A skill for converting written film scripts into production-ready Seedance 2.0 video generation prompts, using its image reference system for visual consistency across shots.

When to use this skill
User pastes a film script / screenplay excerpt and attaches reference images
User asks for "shot prompts," "Seedance prompts," or "video generation prompts" from text
User wants to break narrative into 30-second (or other duration) shots
User wants to chain shots together using last-frame → first-frame continuity
User asks to refine, rewrite, or iterate on an existing Seedance prompt's visual or audio elements
Core workflow
Parse the script — Identify scene boundaries, character entrances, and atmospheric beats. Map these to a shot list (typically 2–4 shots of 30s each for a single scene, but adjust to the material).
Identify references — Match attached images to their kind (character, location, prop, weapon, last-frame plate). All go into the same REFERENCES block.
Write each shot's prompt using the 4-part structure below.
Maintain continuity — When chaining shots, use the previous shot's last frame as the next shot's locked first frame.
Prompt anatomy
Every Seedance prompt has 4 blocks in this exact order:

1. REFERENCES         — all [Image N] tags + their descriptions
2. SHOT DESCRIPTION   — first-frame instruction, camera, action, dialogue, hold, runtime
3. CINEMATOGRAPHY     — lens, DOF, light, stock, grade, movement, visual anchor
4. AUDIO              — dialogue + delivery, silence, ambient, foley, rising, score
Seedance does not distinguish between character, location, or prop image slots — they are all just numbered reference images. Group them under one REFERENCES header in the order they will be invoked in the SHOT DESCRIPTION.

1. REFERENCES
A single block listing every [Image N] tag followed by a dense description of what that image contains. Each image (or group of images of the same subject) gets one paragraph. Reference kinds you'll commonly see:

Character — physical features, build, clothing, expression baseline, with reference-view notes (front/side/profile/full body)
Location — time of day, weather, layers (FG/MG/BG), light sources, palette, atmospheric qualities, grade direction
Prop / weapon / object — material, scale, wear, distinguishing marks, how it's typically held or framed
Last-frame plate — the final composition of the previous shot, described as a still: character positions, key elements visible, lighting state, color temperature
[Image 1] [Image 2] [Image 3] <character A: archetype, age, build, hair, skin, eyes, expression, full clothing inventory with material and wear, consistent across reference views>

[Image 4] [Image 5] <character B: same depth as above>

[Image 6] <location: shot type, time of day, weather, FG/MG/BG layers, lighting sources, palette, atmospheric qualities, grade direction>

[Image 7] <prop: material, scale, wear, distinguishing details>

[Image 8] <last-frame plate: composition match, character positions, key elements, lighting state>
Critical rule: copy/paste descriptions verbatim across shots. When a character, location, or prop appears in multiple shots, the description paragraph must be identical, character for character. Seedance's reference system locks to consistent descriptors — even small rewordings cause drift in faces, clothing, and environments.

What to include in descriptions: concrete visual features only — what the camera sees.

What to leave out: personality, backstory, motivation, mood (unless directly visible as expression).

2. SHOT DESCRIPTION
The action and camera language. Reference images by their [Image N] tag inline when the subject enters frame or takes a major action.

Required elements, in roughly this order:

Opening frame instruction — "STRICTLY open on [Image N] as the locked first frame — held for X seconds..."
Camera language — push-in, pull-back, dolly, locked-off, handheld, whip pan, hard cut, match cut, etc.
Action and blocking — entrances, movements, gestures, eye direction, breath, micro-expressions
Spoken lines — embedded inline in bold quoted format with delivery notes (whisper, growl, clipped, etc.). For dialogue craft and quality, defer to the dialogue-quality-bar skill before locking lines.
Environmental motion — wind, rain, distant elements, light changes
Final frame instruction — what to hold on, for how long, before cutting
Runtime — "30 second total runtime" stated explicitly at the end
3. CINEMATOGRAPHY
Technical and stylistic direction in a single paragraph:

Lens (anamorphic, focal length implication)
Depth of field (shallow, deep, focus pulls)
Lighting (natural, practical, rim, side, hard/soft)
Film stock simulation (35mm grain, etc.)
Color grade (neo-noir teal-and-amber, etc.)
Aspect ratio if non-standard
Camera movement summary
Visual anchor (one saturated color or key element kept consistent across shots)
4. AUDIO
Sound design separate from action:

Spoken lines repeated with delivery direction (close-mic'd, distant, cracked, etc.)
Silences with explicit duration
Ambient sound (wind, water, distant mechanical sounds)
Foley cues (footsteps, clothing rustle, mechanical clicks)
Rising element introduced under the final beat (approaching footsteps, swelling drone)
Score direction — usually "No music score" unless explicitly needed, with sub-bass or drone notes if used
Template
[Image 1] [Image 2] [Image 3] <character A: dense visual description>
[Image 4] [Image 5] <character B: dense visual description>
[Image 6] <location OR last-frame plate description>
[Image N] <any additional props, weapons, vehicles, etc.>

SHOT DESCRIPTION: STRICTLY open on [Image N] as the locked first frame — held for X seconds [match-composition specifics]. [Camera move] → [action] → **"spoken line"** → [silence X seconds] → [action] → **"spoken line"** → [final hold X seconds]. X second total runtime.

CINEMATOGRAPHY: [lens, DOF, lighting, stock, grade, movement, anchor]

AUDIO: [spoken lines with delivery notes, silence durations, ambient, foley, rising element, score]
Visual anchor principle
Identify one saturated color or visual element to anchor every shot a character appears in. Example: a character in red vinyl against a teal-and-amber industrial palette — that red must stay saturated through the grade direction in every shot description. This gives the cut sequence visual continuity even when framing changes.

Common pitfalls to avoid
Don't split references into separate header blocks for characters vs. locations vs. props. All references go under one REFERENCES section — Seedance treats every [Image N] slot the same way.
Don't add unmotivated atmospheric beats at shot endings. A floodlight clicking on, a bird flying past, a distant horn — these clutter the close of a shot. The ending should hold on the strongest single element (a face, an approaching sound, a final spoken beat). Anything else competes for attention.
Don't reword reference descriptions between shots. Copy/paste the exact paragraph. Seedance's reference system rewards consistency.
Don't over-describe action. Seedance reads short concrete verbs better than purple prose. "She turns to face him" beats "She rotates her entire body in a balletic motion of deliberate confrontation."
Don't forget the runtime statement. Always include "X second total runtime" explicitly.
Don't bury spoken lines inside paragraphs. Bold every line: **"Like this."** It helps the model parse and helps the user QA the script.
Don't use markdown headers inside the prompt body. Seedance prompts are paragraph blocks with bold inline labels (SHOT DESCRIPTION:, CINEMATOGRAPHY:, AUDIO:).
Iteration patterns (visual & audio only)
When a user pushes back on a draft prompt's visual or audio elements:

"Why is X happening at the end?" → A leftover beat from an earlier draft is cluttering the close. Cut it cleanly; the strongest ending uses one element, not three.
"Continue this shot, here's the last frame" → Treat the attached frame as a new reference image. Describe it as a last-frame plate in REFERENCES, then open the next shot with "STRICTLY open on [Image N] as the locked first frame..."
Audio feels cluttered → Strip non-essential foley. Keep wind, one mechanical/ambient sound, and the speech track. Add the rising element (approaching footsteps, swelling drone) only under the final beat.
Character looks different between shots → The description paragraph drifted. Restore the exact original paragraph verbatim.
Continuity feels off → The "STRICTLY open on [Image N] as locked first frame" line is missing or the last-frame plate description doesn't match the prior shot's closing composition. Re-anchor with precise composition specifics.
For dialogue line problems (mechanical phrasing, lines that "sound bad," requests for alternative spoken lines), defer to the dialogue-quality-bar skill rather than attempting to fix dialogue inside this skill.

Output format
Always present the final prompt as plain prose blocks (not code fences) so the user can copy directly into Seedance. Use bold inline labels (SHOT DESCRIPTION:, CINEMATOGRAPHY:, AUDIO:) rather than markdown headers. Keep spoken lines bolded and in straight double quotes. End SHOT DESCRIPTION with the runtime statement.
