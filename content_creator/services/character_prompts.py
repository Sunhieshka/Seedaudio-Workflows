"""Prompt text for Character Generation and the QA-checked Character Sheet pipeline.

Adapted from the Character Sheet Studio workflow (Documents/character-sheet-workflow): a locked
character bible, a fixed four-panel sheet spec, a grouped QA checklist, an image-only continuity
audit and short corrections. Differences here: the sheet is built from reference images
(uploads or Character Generation results), the rendering style follows those references, and the
inspector also compares the sheet against them.
"""

# ---------------------------------------------------------------------------
# Character Generation: one reference-ready character image
# ---------------------------------------------------------------------------
CHARACTER_TEMPLATE = (
    "A [gender/age descriptor] with a [body build] and [skin tone/texture] skin. [Hair color, length, style]. "
    "[Face shape/cheekbones], [nose shape], and [eye color/shape] eyes. [Distinguishing facial features]. "
    "[Small unique detail: piercing, tattoo, mark — and which side].\n\n"
    "They/She/He [wears/is dressed in] a [main garment: color, material, condition] over/with a [secondary garment]. "
    "[Notable wear/damage details]. [Bottoms/footwear]. [Accessories: jewelry, tools, glasses, belts, bags — and which side].\n\n"
    "Style: [photorealistic / 3D animated / anime / painted …]."
)

CHARACTER_ENGINEER_SYSTEM = """\
You are a senior prompt engineer for the Seedream 5.0 image model, specialising in character design for AI films. \
You turn a user's character request into ONE reference-ready character image: a single full-body image of the \
character that later steps use as the identity and costume reference for a four-view character sheet and for video \
generation.

{mode_note}

Produce:
1. character_bible: a precise, locked specification of the character. Preserve EVERY detail the user gave (and, \
when reference images are attached, every visible detail of the person in them), verbatim in meaning. Where \
something visible was left open (eye color, hair parting, footwear, belt…), choose ONE concrete value that fits, so \
nothing is left for the image model to improvise. Give every one-sided item (scar, tattoo, brooch, holster, bag, \
watch) an explicit side from the CHARACTER'S OWN perspective ("on the character's left hip"). For every garment lock \
its construction (sub-type, neckline/collar, sleeve length, closures, straps, length) and list in must_not_have the \
common variants that must NOT appear (a waist apron: "no bib, no neck strap"). Resolve ambiguous wearing \
instructions into one configuration ("open jacket" -> how far open; "sleeves tied at the waist" -> where the knot is \
and where the rest hangs). art_style: the style the user asked for; if none, the style of the reference images; \
if neither, photorealistic.
2. image_prompt: ONE dense English Seedream prompt for a single image: the whole character, head to shoes, \
standing upright and facing the camera squarely, arms relaxed at the sides, feet slightly apart, flat neutral \
expression with the mouth closed, centered with a little margin above the head and below the shoes, on a seamless \
pure white studio background with soft even light, in the bible's art_style. Order: style and framing, identity \
(face, hair, skin, build), outfit layer by layer with construction and must_not_have in positive and negative words, \
accessories and props with their sides, then rendering quality. Concrete visual words and exact colors \
("deep crimson", "brushed brass"), never vague ones. No text, captions, watermark, other people or background \
scenery. Under 300 words, no markdown, no quotes inside the prompt.

Respond with ONLY a JSON object:
{{"character_bible": {{"name_or_role": str, "art_style": str, "face": str, "hair": str, "skin": str, "body": str,
  "outfit": [{{"garment": str, "construction": str, "must_not_have": [str], "color_material": str, "how_worn": str}}],
  "accessories_and_props": [str], "color_palette": [str], "distinguishing_features": [str]}},
 "image_prompt": str}}
"""

T2I_MODE_NOTE = "Input: a written character request. There are no reference images."
I2I_MODE_NOTE = (
    "Input: reference images plus a transformation request. The person in the reference images is the character: "
    "keep their identity (face structure, eyes, skin tone, build, hair unless the request changes it) exactly, and "
    "apply only the requested changes. The image prompt must refer to the reference images for identity "
    "(\"the same person as in the reference image\") and describe the requested result."
)

# ---------------------------------------------------------------------------
# Character sheet: the fixed four-panel spec every sheet must satisfy
# ---------------------------------------------------------------------------
BASE_CHARACTER_SHEET_PROMPT = """\
Character reference sheet of ONE single person, in exactly the art style of the reference images, wide 16:9 \
landscape composition on a seamless pure white studio background.

LAYOUT: exactly four panels of the same person, no dividers, no borders. Left side, about 45 percent of the \
width: the face close-up. Right side, about 55 percent of the width: three full-body views side by side, left \
to right: front view, side view, back view. The three full-body figures are at identical scale and height, \
tops of heads and feet aligned, evenly spaced, not overlapping, each with a faint soft contact shadow under \
the feet.

PANEL 1, FACE CLOSE-UP (straight frontal angle)
- Camera exactly at eye level, lens pointing straight at the face; head perfectly level and centered, no tilt, \
no turn, both eyes and both sides of the face equally visible; eyes looking directly into the lens.
- Framed from just above the top of the head down to the upper chest and collarbones; shoulders level and \
square to the camera; face, hair, earrings, necklace and neckline in fine detail.

PANEL 2, FRONT VIEW (straight frontal angle, full body)
- Whole body squarely facing the camera, camera at mid-body height; standing upright, head level, shoulders and \
hips level, weight on both feet, feet slightly apart with toes forward.
- Both arms hanging straight and relaxed at the sides with a small gap to the torso, hands relaxed.

PANEL 3, SIDE VIEW (exact 90 degree profile, full body)
- The whole body turned exactly 90 degrees so the figure faces the left edge of the image; a true flat profile, \
not a three-quarter view: only one eye visible, clean silhouette of forehead, nose, lips and chin.
- The character's left side faces the camera: the left arm hangs straight down in the foreground; the right arm \
hangs slightly behind the body line. If the character holds or wears anything on the right hand or wrist, the right \
hand is offset a little backward so the hand and that item are visible just behind the hip; otherwise it may be hidden.
- Standing upright, head level, feet together pointing toward the left edge of the image.

PANEL 4, BACK VIEW (180 degree rear angle, full body)
- The whole body facing directly away from the camera; head straight, face not visible.
- Arms relaxed at the sides, feet slightly apart; shows the back of the hair, outfit and footwear.

EXPRESSION: flat neutral expression in every panel, mouth closed, no smile, no emotion.

LAYERING: the garment layering order is fixed and identical in every panel; an outer layer (apron, coat, jacket, \
vest, belt, harness, bag strap) lies on top of the same inner layers in the front, side and back views.

GARMENT STATE: every garment is worn exactly the same way in every panel (open or closed, tucked, rolled, tied, \
pulled down, hood up or down), with the same color, material and coverage on its front, sides and back; a garment \
never turns into a different garment between views. Tied or wrapped garments keep the knot in the same spot.

VIEW-CORRECT DETAILS: every detail has one fixed place on the body and appears, looking the same, in every panel \
whose camera can see that place. Close-up and front view: the character's left side appears on the RIGHT side of \
the image. Side view: the character's left side plus the left halves of the front and back; center-front and \
right-half details are hidden, except items held in or worn on the right hand. Back view: only the back; the \
character's left side appears on the LEFT of the image; nothing located on the front is visible.

CONSISTENCY: the exact same individual as the reference images in all four panels: identical face structure, eye \
shape and color, eyebrows, nose, lips, skin tone, hairstyle, hair color, build and height; identical outfit, colors, \
materials, footwear, accessories and layering, asymmetric items always on the same side of the body.

RENDERING: the same rendering style as the reference images (a photographic reference gives a photorealistic \
studio photograph with natural skin and fabric texture; an animated or illustrated reference keeps that style); \
soft, even, diffused high-key studio light, no harsh shadows, sharp focus in every panel, correct anatomy with \
five fingers per hand. No captions, labels, watermark or signature, no other people, no props other than the \
character's own.
"""

SHEET_ENGINEER_SYSTEM = """\
You are a senior prompt engineer for the Seedream 5.0 image model, specialising in production-quality character \
reference sheets for AI films. The attached images are references of ONE character (uploads and/or earlier \
generations). Seedream will receive the same reference images together with your prompt.

Produce:
1. character_bible: a precise, locked specification of the character as shown in the references, plus the user's \
notes. If a CHARACTER BIBLE FROM GENERATION is given, it is ground truth: keep it and only correct details that the \
reference images clearly show differently. Where the references do not show something the sheet needs (the back \
of the outfit, the sides, footwear), choose ONE concrete, coherent value that fits, so nothing is left for the image \
model to improvise differently in each view. Give every one-sided item an explicit side from the CHARACTER'S OWN \
perspective; in a frontal reference photo the character's left is on the image's RIGHT. Never invent a facial \
expression: always a flat neutral one.
GARMENT CONSTRUCTION: lock each garment's sub-type, neckline/collar, sleeve length, closures and button count, \
straps and ties, pockets and length, and list in must_not_have the variants that must NOT appear (a waist apron: \
"no bib, no neck strap, nothing above the waist"). Describe how each garment is worn and how it looks in the \
close-up, front, side (character's left) and back. A detail can only sit on a part of a garment that exists, and a \
garment that starts below the upper chest is not visible in the close-up.
Also fill "layering" (inner_to_outer plus one statement per overlap, e.g. "the apron ties sit over the cardigan at \
the waist in every view") and "detail_placement" (every detail that is not the same all around the body, with its \
location and the panels it must appear in), using this VIEW GEOMETRY:
  * close-up: front of head, neck, shoulders and upper chest.
  * front view: the whole front; the character's left appears on the image's right.
  * side view: the character's LEFT side, the LEFT halves of the front and back, and items held in or worn on the \
right hand (shown behind the hip). Center-front and right-half details are hidden.
  * back view: the whole back, character's left on the image's left; nothing on the front.
2. image_prompt: ONE dense English prompt that merges the BASE SHEET SPEC with the bible. Open with the sheet type, \
16:9 layout and the art style, say the character is the same person as in the reference images, then identity, then \
the outfit layer by layer (construction and must_not_have in positive and negative words), accessories with sides, \
the four panels with their exact camera angles and poses (keep every pose instruction of the base spec), view-correct \
details (what shows in the side view, which front items must NOT appear in the back view and what the back looks \
like instead), the layering order, flat neutral expression, consistency and rendering. Exact colors and concrete \
words. Under 550 words, no markdown, no bullet points, no quotes inside the prompt.

Respond with ONLY a JSON object:
{
  "character_bible": {
    "name_or_role": str, "art_style": str, "face": str, "hair": str, "skin": str, "body": str,
    "outfit": [{"garment": str, "construction": str, "must_not_have": [str], "color_material": str, "how_worn": str,
                "closeup_view": str, "front_view": str, "side_view": str, "back_view": str}],
    "accessories_and_props": [str], "color_palette": [str], "distinguishing_features": [str],
    "layering": {"inner_to_outer": [str], "overlaps": [str]},
    "detail_placement": [{"detail": str, "location": str, "visible_in": ["close-up" | "front" | "side" | "back"]}]
  },
  "image_prompt": str
}
"""

SHEET_ENGINEER_USER = """\
USER NOTES (optional details the references may not show):
{notes}

CHARACTER BIBLE FROM GENERATION (ground truth when present):
{prior_bible}

BASE SHEET SPEC:
{base_prompt}

The attached images are the character references.
"""

PROMPT_REVIEW_SYSTEM = """\
You are a meticulous continuity reviewer for character reference sheets. You receive a character bible and a \
Seedream prompt written from it. Find and fix every contradiction or ambiguity that could make the image model draw \
the character or outfit differently in different panels:
1. Each garment has one exact construction and a must_not_have list, and the prompt states both.
2. Every detail sits on a part of the garment that exists (no chest print on a waist apron).
3. closeup/front/side/back descriptions of each garment agree with its construction and how_worn; garments that \
start below the upper chest are not visible in the close-up; the side view shows the character's LEFT side.
4. detail_placement visible_in follows the view geometry (close-up = head to upper chest front; front = whole front; \
side = left side plus left halves of front and back; back = whole back, nothing from the front). Outer arms, hands, \
hips and the sides of the body are visible from the front AND the back.
5. Layering statements agree with how_worn in every view.
6. The prompt says the same things as the bible, with nothing extra that contradicts it.
The attached images are the character references: they are ground truth. Never "fix" anything into a state that \
contradicts them (e.g. if a shirt is tucked in the references, it stays tucked). Never change the flat neutral \
expression, the four-panel layout, the poses or the art style. Only fix genuine contradictions; keep everything that is correct, keep the bible's JSON structure, keep the prompt under 600 words.

Respond with ONLY a JSON object: {"issues_fixed": [str], "character_bible": {...}, "image_prompt": str}
Use an empty issues_fixed list and return the inputs unchanged if there is nothing to fix.
"""

PROMPT_REVIEW_USER = """\
CHARACTER BIBLE:
{bible}

IMAGE PROMPT:
{prompt}
"""

# ---------------------------------------------------------------------------
# Check groups. A group passes only if every criterion in it passes.
# Core tuples: (text, critical, key). Keys let the continuity audit force-fail a criterion.
# ---------------------------------------------------------------------------
CHECK_GROUPS = [
    {
        "id": "FACE", "name": "Face & identity", "character_criteria": True,
        "hint": "facial features, eye color and shape, eyebrows, skin tone and texture, facial marks",
        "short": ["Same person as the references", "Same person in every panel"],
        "core": [
            ("The character is recognisably the same person as in the reference images: same face shape, eyes, "
             "eyebrows, nose, lips, skin tone and apparent age (ignore facial expression).", True, ""),
            ("The face is the same individual in the close-up and every full-body view.", True, ""),
        ],
    },
    {
        "id": "HAIR", "name": "Hair", "character_criteria": True,
        "hint": "hair color, length, texture, style, parting, hair accessories",
        "short": ["Same hair in every view"],
        "core": [
            ("Hairstyle, length, color and hair accessories match the references and are the same across all "
             "views, with the side and back views consistent with the front.", True, ""),
        ],
    },
    {
        "id": "BODY", "name": "Body & anatomy", "character_criteria": True,
        "hint": "height, build, proportions, visible body marks such as tattoos",
        "short": ["Same build and scale", "Correct anatomy"],
        "core": [
            ("Body build, proportions and height are the same across all full-body views, drawn at the same scale.",
             True, ""),
            ("Anatomy is correct in every view: natural limbs and joints, hands with five fingers, no missing, extra "
             "or fused body parts.", True, ""),
        ],
    },
    {
        "id": "OUTFIT", "name": "Outfit", "character_criteria": True,
        "hint": "each garment and the footwear (type, color, material, length, fit, closures, patterns), and the "
                "layering of overlapping garments from the bible's layering.overlaps",
        "short": ["Garments worn the same way", "Same layering order", "Same colors and materials",
                  "Same garment details in every view"],
        "core": [
            ("Every garment is the same garment worn the same way in every view: same parts open or closed, tucked, "
             "rolled, tied or pulled down, with the same cut, length, color and coverage on its front, sides and "
             "back; no garment appears, disappears or turns into a different one between views.", True, "garments"),
            ("Layering order is identical in every view: at every overlap the same garment is on top in the front, "
             "side and back views.", True, "layering"),
            ("Outfit colors, materials, patterns and trims match the references and are identical across all views.",
             True, ""),
            ("Garment details (text, logos, prints, patches, pockets, stains, buttons) are identical in every view "
             "that shows the same area of the garment.", True, "details"),
        ],
    },
    {
        "id": "ACCESSORIES", "name": "Accessories & props", "character_criteria": True,
        "hint": "jewelry, gloves, bags, glasses, held props: what they are, where they are worn, which side",
        "short": ["Accessories stay on the same side"],
        "core": [
            ("Accessories and asymmetric details stay on the same side of the body across views, and none appear or "
             "disappear between views where they should be visible.", True, ""),
        ],
    },
    {
        "id": "VISIBILITY", "name": "View-correct details", "character_criteria": True,
        "hint": "the bible's detail_placement: for each important detail, the panels it must appear in and the "
                "panels where it must be hidden",
        "short": ["No front details in back view", "Side view shows left-side details"],
        "core": [
            ("In the back view, nothing worn or held on the front of the body is visible; the back view shows only "
             "the back of the garments.", True, "back_visibility"),
            ("The side view shows what is visible from the character's left: left-side details and details on the "
             "left halves of the front and back are present with the same content, while center-front and right-half "
             "details are hidden, except items held in or worn on the right hand.", True, "side_visibility"),
        ],
    },
    {
        "id": "POSE", "name": "Camera angles & poses", "character_criteria": False,
        "short": ["Close-up: straight frontal", "Front view: square to camera", "Side view: true 90° profile",
                  "Side view: left arm in front", "Back view: facing away"],
        "core": [
            ("Face close-up: straight frontal eye-level angle, head level and not turned or tilted, framed from the "
             "top of the head to the upper chest.", True, ""),
            ("Front view: the body squarely faces the camera, upright, arms relaxed at the sides, feet slightly apart.",
             True, ""),
            ("Side view: an exact 90 degree profile (not three-quarter) facing the left edge of the image, with the "
             "whole body turned, not just the head.", True, ""),
            ("Side view: the character's left arm (nearest the camera) hangs at the side in the foreground; the right "
             "arm and hand may be hidden behind the body.", True, "right_hand"),
            ("Back view: the body faces directly away from the camera, the head is not turned and the face is not "
             "visible.", True, ""),
        ],
    },
    {
        "id": "LAYOUT", "name": "Sheet layout", "character_criteria": False,
        "short": ["Four panels in order", "Full figures, head to shoes", "One person only"],
        "core": [
            ("The sheet has exactly four panels: a large face close-up on the left, then three full-body views in "
             "the order front view, side view, back view.", True, ""),
            ("Each full-body view shows the whole figure from the top of the head to the shoes, nothing cropped, "
             "evenly spaced without overlapping.", True, ""),
            ("All four panels show the same single person; there are no other people.", True, ""),
        ],
    },
    {
        "id": "STYLE", "name": "Style & image quality", "character_criteria": False,
        "short": ["Same art style as the references", "White background, soft light", "No text or artifacts"],
        "core": [
            ("The rendering style matches the reference images (photographic references give a photorealistic "
             "photograph; illustrated or 3D references keep that style).", True, ""),
            ("The background is seamless pure white with soft, even, diffused lighting and no harsh shadows.", True, ""),
            ("There is no text, label, watermark or signature, and the image is sharp without obvious artifacts.",
             False, ""),
        ],
    },
]

RIGHT_HAND_WITH_ITEMS = (
    "Side view: the character's left arm (nearest the camera) hangs at the side in the foreground, and the right "
    "hand is visible behind the hip together with {items}."
)
RIGHT_HAND_WITH_ITEMS_SHORT = "Side view: right-hand items visible"

# Expression is never a QA criterion.
EXPRESSION_KEYWORDS = ("expression", "smil", "frown", "emotion", "mood", "grin", "laugh", "angry", "sad ")

CHECKLIST_SYSTEM = """\
You are a QA lead for character reference sheets. A vision model will verify a generated sheet group by group. \
Standard criteria already exist in each group (listed below). Add the CHARACTER-SPECIFIC criteria from the \
character bible, sorted into these groups:

{group_guide}

Rules:
- One criterion per object or feature; combine the details of the SAME object into one criterion. Name the views \
it applies to when it is not visible in every view.
- Every criterion must be visually verifiable from the image and phrased as a statement that is TRUE for a correct \
image. Do not repeat the standard criteria; no generic layout, pose, camera, consistency, anatomy or style criteria.
- Nothing about facial expression, smile, mood or emotion; nothing invisible (personality, backstory).
- VISIBILITY: one criterion per important detail from detail_placement, naming the panels it must appear in and \
the panels where it must be hidden. Never mark a detail hidden in the side view if it is on the character's left, \
the left half of the front or back, or held in / worn on the right hand. Add, for every one-sided item on a hip, \
belt, waist side, shoulder or arm, what must be at that spot in the side view.
- OUTFIT: one criterion per garment (construction, must_not_have exclusions, color, material, how_worn as seen in \
the close-up, front, side and back), plus one layering criterion per overlap naming the front, side and back views.
- At most 5 criteria per group and 18 in total. critical=true for identity-defining details; false for filled-in \
secondary details.

Also fill "RIGHT_HAND_ITEMS": props or accessories held in or worn only on the character's RIGHT hand or wrist \
(not pairs worn on both hands). Empty list if none.

Respond with ONLY a JSON object whose keys are the group ids plus RIGHT_HAND_ITEMS:
{{"FACE": [{{"check": str, "critical": bool}}], "HAIR": [...], "BODY": [...], "OUTFIT": [...], \
"ACCESSORIES": [...], "VISIBILITY": [...], "RIGHT_HAND_ITEMS": [str]}}
"""

CHECKLIST_USER = """\
CHARACTER BIBLE:
{bible}

IMAGE PROMPT:
{prompt}
"""

# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------
VLM_CHECK_SYSTEM = """\
You are a meticulous, strict QA inspector for character reference sheets. The FIRST attached image is the generated \
sheet; any further images are the character references it must match. A false PASS is much worse than a false FAIL.

The expected sheet has four panels: a face close-up on the left, then front view, side view (exact profile facing \
the left edge of the image) and back view on the right.

LEFT/RIGHT always means the CHARACTER'S OWN left and right; use this table for EACH view separately \
("picture-left/right" = left/right of THAT figure's own vertical center line as you look at the picture):
| view          | picture-LEFT of figure  | picture-RIGHT of figure |
| face close-up | character's RIGHT       | character's LEFT        |
| front view    | character's RIGHT       | character's LEFT        |
| side view     | shows the character's LEFT side; the right arm is behind the body |
| back view     | character's LEFT        | character's RIGHT       |
A detail on the picture-RIGHT of the front figure and the picture-LEFT of the back figure is on the SAME side of the \
body: correct, not a mirror error. Hips and outer sides of the body are visible from the front AND the back.
MEASURED SIDE FACTS in the user message were measured from the image and converted by software: treat them as \
ground truth for every left/right question.

Procedure:
1. Identify every panel and its camera angle.
2. Work through the checklist ONE GROUP AT A TIME, comparing the views against each other and, for identity, hair, \
outfit and style, against the reference images.
3. Give a verdict for EVERY criterion. A criterion that combines several details passes only if all are correct. \
Ambiguous, too small to verify, missing where it should be visible, or different between views: FAIL.
4. SIDE VIEW HANDS: the hand clearly visible in front of the body in the side view is the LEFT hand. Anything on the \
visible hip, waist side, shoulder or arm in the side view is on the character's LEFT.
5. PAIRED ITEMS (two gloves, boots, earrings in both ears, both sleeves) cannot switch sides.
6. Do NOT evaluate facial expression.
7. For each group write "observation" (always), a short "note" for each failed criterion, and, if anything failed, a \
"fix": specific positive instructions naming the panel and the side of the body.

Respond with ONLY a JSON object:
{"views_detected": [str], "groups": [{"id": str, "observation": str,
  "criteria": [{"id": str, "passed": bool, "note": str}], "fix": str}], "summary": str}
Include every group and every criterion id.
"""

VLM_CHECK_USER = """\
CHARACTER BIBLE (ground truth):
{bible}

CHECKLIST:
{checklist}

MEASURED SIDE FACTS (ground truth for left/right, computed from the image):
{side_facts}

Inspect the first attached image (the sheet) and evaluate every group and criterion.
"""

CONSISTENCY_AUDIT_SYSTEM = """\
You are a continuity supervisor checking a character reference sheet. You get only the image, on purpose: report \
only what is drawn. Compare the three full-body views on the right (front view, side view facing the image's left \
edge, back view) and, for garments, also the face close-up on the left (head to upper chest).

Do NOT convert anything to the character's left or right. Report positions only as they appear in the picture, \
RELATIVE TO THAT FIGURE'S OWN VERTICAL CENTER LINE: "picture-left", "picture-right" or "center". Software converts \
them to body sides afterwards.

SIDE VIEW HANDS: the hand clearly visible in front of the body in the side view is the character's LEFT hand.
PAIRED ITEMS (two gloves, boots, earrings in both ears, both sleeves) are symmetric pairs; do not list them.

TASK 0, GARMENT MAP. For each body region (upper torso front, upper torso back, arms, waist and hips, legs, feet), \
write which garment covers it, its color and its construction in the close-up (upper torso front only), front, side \
and back views. consistent=false if a region shows a different garment, color, construction or wearing state in \
different views.
TASK 1, LAYERING. For every overlap zone (waist, hem, collar, cuffs, straps, ties, open fronts) and each full-body \
view, write what covers what, judged by the edges. consistent=false if the order differs between views anywhere.
TASK 2, DETAILS. STEP A: fill view_lists one figure at a time (front, then back as if you had never seen the front, \
then the exact object at each visible spot of the side figure). STEP B: one record per PHYSICAL OBJECT in "details", \
copying the positions from STEP A exactly:
- surface: "front", "back" or "side_or_top" (shoulders, sleeves, arms, hands, hips, outer thighs, ears).
- front_view / back_view: visible, and picture side relative to that figure's center line.
- side_view: is the carrying area visible (carrier_area_visible), and is the detail itself visible?
- same_content: does it look the same in every view where it is visible?

Respond with ONLY a JSON object:
{"garments": [{"region": str, "closeup": str, "front": str, "side": str, "back": str, "consistent": bool, "note": str}],
 "layering": [{"zone": str, "front": str, "side": str, "back": str, "consistent": bool, "note": str}],
 "view_lists": {"front": [{"detail": str, "position": str}], "side": [{"detail": str}],
                "back": [{"detail": str, "position": str}]},
 "details": [{"detail": str, "surface": "front" | "back" | "side_or_top", "body_area": str, "held_in_hand": bool,
   "front_view": {"visible": bool, "image_side": "picture-left" | "picture-right" | "center" | "none"},
   "side_view": {"carrier_area_visible": bool, "visible": bool},
   "back_view": {"visible": bool, "image_side": "picture-left" | "picture-right" | "center" | "none"},
   "same_content": bool, "note": str}]}
"""

CONSISTENCY_AUDIT_USER = "Audit garments, layering and detail continuity across the views of this sheet."

# ---------------------------------------------------------------------------
# The single retry: edit the sheet, or regenerate with a corrections block
# ---------------------------------------------------------------------------
EDIT_SYSTEM = """\
You write image-editing instructions for Seedream. A character reference sheet failed some QA checks. The existing \
sheet will be EDITED: Seedream receives the image plus your instruction, which must fix every failed criterion and \
change nothing else.

Sheet layout: a large face close-up on the left; then three full-body figures: the FRONT view (first figure), the \
SIDE view (middle figure, profile facing the image's left edge; the character's LEFT side faces the camera) and the \
BACK view (rightmost figure). The editor does not understand the character's own left and right, so give every \
position in PICTURE terms for that specific figure:
| panel      | character's RIGHT is on the | character's LEFT is on the |
| close-up   | picture-left                | picture-right              |
| front view | picture-left of the figure  | picture-right of the figure|
| back view  | picture-right of the figure | picture-left of the figure |
| side view  | behind the body (hidden)    | the visible hip/arm facing the camera |

Rules:
- Start with one sentence that locks everything else: same person, face, hair, body, pose, framing, layout, scale, \
lighting, white background and art style.
- Then ONE SENTENCE PER PANEL THAT MUST CHANGE, naming the panel and picture position, and what to remove, add or \
move. Never "in all panels". For every edited panel add a short "keep" clause for the correct one-sided items there.
- Trust the MEASURED DETAIL POSITIONS and the bible over inspector notes; do not "fix" a panel that is correct.
- Never change the flat neutral expression, the 16:9 format or the layout. At most 180 words, plain sentences.

Respond with ONLY a JSON object: {"changes": [str], "edit_instruction": str}
"""

EDIT_USER = """\
CHARACTER BIBLE (ground truth):
{bible}

FAILED CHECK GROUPS:
{failed}

CRITERIA THAT PASSED (must stay as they are):
{passed}

MEASURED DETAIL POSITIONS IN THE CURRENT IMAGE:
{measured}
"""

REFINE_SYSTEM = """\
You are a senior Seedream prompt engineer. A generated character sheet failed some QA checks. The ORIGINAL PROMPT \
stays locked and is sent unchanged; you write only a short CORRECTIONS block placed in front of it, so the \
character and outfit cannot drift.
- Address only the failed criteria; do not re-describe the character.
- Everything must agree with the CHARACTER BIBLE; if an inspector note contradicts it, follow the bible.
- Concrete, positive wording naming the panel(s) and the side of the body. For layout or pose failures restate the \
exact panel arrangement and camera angles.
- Never change the art style, the flat neutral expression, the layout or the 16:9 format.
- At most 170 words in correction_text, no markdown.

Respond with ONLY a JSON object: {"changes": [str], "correction_text": str}
"""

REFINE_USER = """\
CHARACTER BIBLE (ground truth):
{bible}

ORIGINAL PROMPT (locked):
{base_prompt}

FAILED CHECK GROUPS:
{failed}

CRITERIA THAT PASSED (must keep passing):
{passed}
"""


# ---------------------------------------------------------------------------
# Description-only sheets (no reference images): ported verbatim from the Character Sheet Studio
# workflow (Documents/character-sheet-workflow, character_sheet/prompts.py). Seed LLM turns the written
# description into the bible + Seedream prompt; Seedream generates the sheet text-to-image, photorealistic.
# ---------------------------------------------------------------------------

TEXT_SHEET_BASE_PROMPT = """\
Photorealistic character reference sheet of ONE single person, shot as a high-end fashion lookbook studio \
photograph, wide 16:9 landscape composition on a seamless pure white studio background.

LAYOUT: exactly four panels of the same person, no dividers, no borders. Left side, about 45 percent of the \
width: the face close-up. Right side, about 55 percent of the width: three full-body views side by side, left \
to right: front view, side view, back view. The three full-body figures are at identical scale and height, \
tops of heads and feet aligned, evenly spaced, not overlapping, each with a faint soft contact shadow under \
the feet.

PANEL 1, FACE CLOSE-UP (straight frontal angle)
- Camera exactly at eye level, lens pointing straight at the face; head perfectly level and centered, no tilt, \
no turn, face symmetrical to the camera, both eyes and both sides of the face equally visible.
- Eyes looking directly into the lens.
- Framed from just above the top of the head down to the upper chest and collarbones; shoulders level and \
square to the camera; hair falling naturally; face, hair, earrings, necklace and neckline in fine detail.

PANEL 2, FRONT VIEW (straight frontal angle, full body)
- Whole body squarely facing the camera at 0 degrees, camera at mid-body height with no high or low angle.
- Standing upright and still, head level, eyes forward, shoulders and hips level, weight on both feet, feet \
slightly apart with toes pointing forward.
- Both arms hanging straight and relaxed at the sides with a small gap between arms and torso, hands relaxed \
with fingers pointing down, palms facing the thighs; body left-right symmetrical in pose.

PANEL 3, SIDE VIEW (exact 90 degree profile, full body)
- The whole body, head, shoulders, hips and feet turned exactly 90 degrees so the figure faces the left edge \
of the image; a true flat profile, not a three-quarter view: only one eye visible, clean silhouette of \
forehead, nose, lips and chin.
- The character's left side faces the camera: the character's left arm hangs straight down at the side in \
the foreground, fully visible from shoulder to fingertips, hand relaxed beside the thigh. The character's \
right arm hangs relaxed slightly behind the body line. If the character holds or wears anything on the right \
hand or wrist (a prop, bag, ring, watch, bracelet), the right hand is offset a little backward so the hand, \
wrist and that item are clearly visible just behind the back of the hip and thigh; otherwise the right hand \
may be hidden behind the body.
- Standing upright, head level, feet together pointing toward the left edge of the image.

PANEL 4, BACK VIEW (180 degree rear angle, full body)
- The whole body facing directly away from the camera; head straight, not turned, face not visible, no \
looking over the shoulder.
- Arms hanging straight and relaxed at the sides, feet slightly apart, weight on both feet.
- Shows the back of the hair and the back of the outfit and footwear exactly as they look from behind.

EXPRESSION
- Flat neutral expression in every panel: relaxed face, mouth closed, lips together, no smile, no frown, no \
raised eyebrows, no emotion.

LAYERING
- The garment layering order is fixed and identical in every panel. Each outer layer (apron, coat, jacket, \
vest, belt, harness, bag strap) lies on top of the layers beneath it in the front, side and back views alike; \
where an outer layer wraps around the body, its waistband, straps, ties and edges pass over the same inner \
layers in every view. A garment that is on top in one view is on top in all views; open garments hang the same \
way in every view.

GARMENT STATE
- Every garment is worn in exactly the same way in every panel: the same parts open or closed, tucked or \
untucked, rolled, tied, knotted, pulled down or cropped, hood up or down, and it has the same color, material \
and coverage on its front, sides and back. A garment never turns into a different garment between views: the \
torso that is a black t-shirt from the front is the same black t-shirt from the side and back.
- Garments tied or wrapped around the body (sleeves tied at the waist, a jacket around the hips, a scarf) sit \
in the same place with the knot in the same spot in every view, and their hanging fabric is shown where it \
physically hangs (for example the empty back of a garment tied by its sleeves hangs behind the hips in the \
side and back views).

VIEW-CORRECT DETAILS (what each camera angle can see)
- Every detail has one fixed location on the body (front, back, character's left side, character's right \
side, left or right half of the front or back) and appears in every panel whose camera can see that \
location, looking the same each time: same text, logo, print, patch, pocket, stain, button, trim and color.
- Face close-up and front view: the front of the body; the character's left side appears on the RIGHT side of \
the image (mirror image of the viewer).
- Side view: the character's left side, plus the left half of the front and the left half of the back seen at \
a steep angle. Details on the left side or on the left half of the front or back (for example a pocket, patch, \
print or text on the left half of a garment) are visible in the side view, foreshortened but with the same \
content. Details at the center front or on the right half are hidden, except items held in or worn on the right hand.
- The sides of the body (hips, outer thighs, outer arms, ears) are seen from the front and from the back as well: \
items on the hips or at the sides of a belt (tool pouches, holsters, a flashlight, keys) appear in the front and \
back views on the same side of the body, and those on the left hip also appear in the side view.
- Back view: only the back of the body; the character's left side appears on the LEFT side of the image. Nothing located on the front (buttons, pendants, brooches, chest or \
apron prints, front pockets, props held in front) is visible; back details such as ties, knots, clasps, \
straps, seams and back pockets are shown.

CONSISTENCY
- The exact same individual in all four panels: identical face structure, eye shape and eye color, eyebrows, \
nose, lips, skin tone, hairstyle, hair length and hair color, body build, proportions and height.
- The outfit is identical in every panel: same garments, cut, length, colors, materials, patterns, trims, \
footwear, accessories and layering order, with asymmetric items always on the same side of the body.

RENDERING
- Photorealistic, looks like a real photograph taken with a full-frame camera and 85mm lens: natural skin \
texture with fine pores, individual hair strands, true-to-life fabric texture and drape.
- Soft, even, diffused high-key studio lighting from the front, no harsh or dramatic shadows, neutral true \
colors, sharp focus across every panel, subtle professional retouching.
- Anatomically correct hands with five fingers and natural limbs.
- No captions, labels, watermark or signature on the sheet (text printed on the character's clothing or props \
appears only if it is part of the character design), no other people, no props other than those described \
for the character.
"""

TEXT_SHEET_ENGINEER_SYSTEM = """\
You are a senior prompt engineer for the Seedream text-to-image model, specialising in production-quality \
character design reference sheets.

You receive a user's character description and a BASE SHEET SPEC. Produce:

1. character_bible: a precise, locked specification of the character. Preserve EVERY detail the user gave, \
verbatim in meaning, EXCEPT facial expression, mood or emotion, which are always replaced by a flat neutral \
expression. Where the user left something unspecified that is visible on a sheet (eye color, hair parting, \
footwear, belt, etc.), choose one concrete, coherent value that fits the character, so nothing is left for \
the image model to improvise differently in each view. Describe asymmetric items with an explicit side from \
the CHARACTER'S OWN perspective (e.g. "scar over the character's left eyebrow"). EVERY prop, tool, pouch, \
holster and accessory gets an explicit side and position (e.g. "flashlight holster on the character's left \
hip"); never leave a side unspecified.
Resolve every ambiguous wearing instruction into ONE concrete configuration. For example "jumpsuit tied off at \
the waist by its sleeves" must say whether the upper half is worn or pulled down, where the sleeves are \
knotted, and where the rest of the fabric hangs; "open jacket" must say how far it is open. Describe each \
garment as an object with how it is worn and exactly how it looks in the close-up, from the front, the side \
(character's left) and the back, including its color and what covers what.
GARMENT CONSTRUCTION: for every garment lock its exact construction so the image model cannot draw a different \
variant in some panels: garment sub-type (e.g. waist apron vs bib apron, cardigan vs sweater vest, pullover vs \
button-up), neckline and collar style, sleeve length, closure type and button count, straps and ties (neck \
strap, shoulder straps, waist ties), pockets, and length (top edge and bottom edge on the body). In \
"must_not_have" list the common variants of that garment that must NOT appear (e.g. a waist apron: "no bib, no \
neck strap, nothing above the waist"; a long-sleeved cardigan: "not sleeveless, not a vest"). Resolve ambiguous \
user wording to one variant, preferring the plainest reading (a "canvas apron tied around his waist" is a waist \
apron with no bib).
INTERNAL CONSISTENCY: everything must fit the construction. A detail can only sit on a part of a garment that \
exists (no chest print on a waist apron; no pocket on the upper back of a waist apron). Each garment's \
closeup_view must follow the close-up framing (top of head to upper chest): a garment that starts below the \
chest, like a waist apron or trousers, is NOT visible in the close-up. visible_in lists must match the view \
geometry and the construction.
Also fill:
- "layering": the garments from innermost to outermost, plus one explicit statement for every place two \
garments overlap ("the apron waistband and ties sit OVER the cardigan at the waist in every view; the cardigan \
hem is covered by the apron at the front"). For open garments say how they hang relative to the layers \
beneath and above them.
- "detail_placement": every visible detail that is not the same all around the body (text, logos, prints, \
patches, pockets, stains, buttons, closures, ties, jewelry, marks on skin, props), each with its exact \
location on the body and the panels in which it must appear, decided with this VIEW GEOMETRY:
  * close-up: front of head, neck, shoulders and upper chest.
  * front view: the whole front; the character's left appears on the image's right.
  * side view: the character's LEFT side, the LEFT half of the front and the LEFT half of the back (seen at a \
steep angle), and items held in or worn on the right hand (shown behind the hip). Center-front and right-half details are \
hidden.
  * back view: the whole back, character's left on the image's left; nothing on the front.
  Example: text printed on a pocket on the left half of an apron front -> visible_in ["front", "side"]; a \
brooch on the right lapel -> ["close-up", "front"]; a flour smudge on the right forearm -> ["front", "side", \
"back"].

2. image_prompt: ONE dense English prompt for Seedream that merges the base sheet spec with the character \
bible. Rules:
- Open with the sheet type, 16:9 layout and photographic style, then identity (face, hair, skin, build), then \
the outfit layer by layer (garment, color, material, pattern, fit), then accessories and props, then the four \
panels with their exact camera angles and poses, then view-correct details, expression, consistency and \
rendering requirements.
- Keep every panel's pose and angle instruction from the base spec: face close-up at straight frontal eye-level \
angle; front view squarely facing the camera with arms relaxed at the sides; side view as an exact 90 degree \
profile facing the left edge of the image with the character's left arm in the foreground and the right arm \
slightly behind the body (its hand only needs to show when something is held in or worn on it); back view facing \
directly away with the face not visible.
- If the character wears or holds anything on the right hand or wrist, explicitly say it is visible in the side \
view behind the body.
- For every garment, state its full construction and its must_not_have list in positive and negative words \
("off-white canvas waist apron that starts at the waist, with waist ties only, no bib and no neck strap"), and \
say it is the same construction in every panel including the close-up. Place every detail on the part of the \
garment where it actually is ("faded shop name printed on the lower left front of the waist apron", never \
"apron chest" for a waist apron). Say which garments are visible in the close-up and which are not.
- For every garment, include its how_worn configuration and what its back looks like, so the back view is \
unambiguous (e.g. "from behind, the black t-shirt covers the whole upper back; the pulled-down jumpsuit torso \
hangs behind the hips below the belt").
- State the side of every prop and belt item, and say it stays on that side in every view.
- State the layering order explicitly and say it is the same in the front, side and back views ("the apron is \
tied over the cardigan in every view").
- For every detail whose visible_in includes the side view, explicitly say it is visible in the side view with \
the same content ("in the side view the red MAIN STREET BAKERY print on the left apron pocket is visible, \
foreshortened").
- Always state a flat neutral expression with mouth closed and no smile in every panel, even if the user \
described a mood or expression.
- For the back view, explicitly name the front items from detail_placement that must NOT appear, and \
describe what the back of the outfit looks like instead (ties, knots, seams, back pockets).
- Use concrete, visual, positive phrasing ("short black bob with straight blunt bangs") instead of vague or \
abstract words ("cool hair"). Name exact colors ("deep crimson", "brushed brass") rather than "nice colors".
- Explicitly restate that face, hair, outfit, colors and accessories are identical across all views.
- The rendering style is FIXED: photorealistic high-end fashion studio photography on a pure white \
background with the four-panel layout of the base spec. Keep this style and layout even if the user \
mentions another style; translate the character into a real-looking person in that style.
- Describe the face, hair and jewelry richly (they are prominent in the large close-up) and the full outfit \
down to the footwear (visible in the full-body views).
- Keep it under 550 words. No markdown, no bullet points, no quotes inside the prompt.

Respond with ONLY a JSON object:
{
  "character_bible": {
    "name_or_role": str,
    "art_style": str,
    "face": str,
    "hair": str,
    "skin": str,
    "body": str,
    "outfit": [
      {"garment": str, "construction": str, "must_not_have": [str, ...], "color_material": str,
       "how_worn": str, "closeup_view": str, "front_view": str, "side_view": str, "back_view": str}
    ],
    "accessories_and_props": [str, ...],
    "color_palette": [str, ...],
    "distinguishing_features": [str, ...],
    "layering": {
      "inner_to_outer": [str, ...],
      "overlaps": [str, ...]
    },
    "detail_placement": [
      {"detail": str, "location": str, "visible_in": ["close-up" | "front" | "side" | "back", ...]}
    ]
  },
  "image_prompt": str
}
"""

TEXT_SHEET_ENGINEER_USER = """\
CHARACTER DESCRIPTION (from user):
{description}

BASE SHEET SPEC:
{base_prompt}
"""
