# API Documentation Rules Summary: 1.5 Installed Edition

Material compiled: 2026-10-10; rules reviewed: 2026-10-11. This installed edition is named “1.5” as established by the user. Parts of the supplied new demonstration/requirements documents use other version names; do not rename this edition or claim official API availability on that basis. Formal 1.5 API documentation is not yet available. This file summarizes prompt-expressible capabilities and provisional constraints; it neither invents API specifications nor calls APIs.

## Evidence and status

- [New product demonstrations, complex timelines, and track examples](https://bytedance.larkoffice.com/wiki/GyRDwI6PdiKQ5Mkysz9cGruPnle): primary basis for sound-control writing and demonstrated capabilities.
- [User-provided new requirements document](https://bytedance.larkoffice.com/docx/AqFAdvyaQoyswfxbHJJc1dPlnIh): provisional basis for input combinations, length, and extraction output counts. Requirements material is not a formal API guarantee.
- [Official Dreamina SeedAudio 1.5 introduction](https://dreamina.capcut.com/ai-model/seedaudio-1-5): supplementary product descriptions of audio references, video accompaniment, complex timing, and tracks; not evidence for formal API fields or launch status.

Shared files remain version-independent; this file sets the current package's boundaries at execution. Do not impose old public-API limits of 3000 characters and 3 audio references on this edition; equally, do not advertise tentative new limits as formal API commitments.

## Capabilities usable in optimization

- Natural-language joint creation of voices, music, ambience, and effects.
- Audio references for character timbre and expression: specify character, asset, and desired reference attributes. Users may request timbre, accent, emotion, style, or rhythm references, without a guarantee that arbitrary attributes are fully isolated.
- Video references for dubbing, scoring, and effects, optionally together with voice-reference audio. Let the model use the assets; this skill does not observe them or invent shots/actions.
- Complex time intervals to organize dialogue, music, ambience, and action effects, including meaningful concurrent sounds. Actual adherence has not been tested in this project.
- Specified sound extraction/separation from input source audio. Demonstrations include character vocals, music, ambience, and particular effects; precise source separation is not guaranteed.

**Track scope:** Current material explicitly demonstrates source audio plus an extraction prompt producing specified tracks. This does not establish “one brand-new creation delivering both a mix and every separate track file at once.” If the latter is core, clarify that current material does not confirm that delivery workflow. If the user only wants a well-layered mix, directly optimize prominence and relationships.

## Confirmed generation duration and provisional input/output constraints

| Item | Rule in new materials | Optimization handling |
| --- | --- | --- |
| Generation duration | User-confirmed maximum: 6 minutes, or 360 seconds | Total duration and every interval endpoint must be within 360 seconds. Clarify shortening or segmentation if exceeded; never truncate automatically. |
| Prompt | Requirements document text field: at most 10000 characters; longer text may be automatically truncated | Use 10000 as this package's provisional checking ceiling. Do not rely on truncation; clarify splitting if faithful compression still exceeds it. |
| Audio references | Up to 6; demonstrations describe a 6-minute audio-reference length capability | Provisionally organize at most 6 references. Do not infer generation-duration or total-asset-duration guarantees from that 6-minute reference rule; precise measurement boundaries await formal API confirmation. |
| Video references | Up to 1, usable together with audio references | Preserve user video tags and sound responsibilities; invent no asset properties. |
| Image references | Requirements document: up to 1, not simultaneous with audio/video inputs | Not a primary task; do not add proactively. Clarify explicit conflicting requests. |
| Source-audio extraction outputs | Requirements document: one or multiple tracks, up to 6 | Organize user-specified outputs when targets are clear. Clarify more than 6; do not silently combine targets. |
| Asset formats/sizes and reference fields | No complete formal API rules confirmable in this project | Do not inherit old parameters or invent new values. Do not question ordinary creative users merely because fields are unknown. |

The user has explicitly confirmed a 6-minute (360-second) generation maximum for this edition; enforce it as a confirmed rule. Audio-reference length remains a separate asset rule and must not be confused with generation duration. Final prompts may contain creative total duration, but not this table's field names, limit explanations, or edition names.

## Timing feasibility check

Follow the shared guide: total duration and interval endpoints within 360 seconds; line capacity compatible with pace, pauses, and nonverbal performance; enough time for serial actions and explicit silence. Do not default unspecified duration to 6 minutes. Clarify segmentation only when content clearly cannot finish naturally within the maximum. Being below the maximum does not itself establish a feasible schedule.

## Reference principles

Examples refer to existing audio with `@音频1` etc. Normalize optimizer output to `@音频X`: normalize established references such as `{{Audio 1}}` or `@Audio1` to `@音频1`, preserving original number and role. This is this skill's output convention, not a claim that every entry point accepts only this syntax. Use `@音频1` for one known unnumbered audio asset; clarify multiple unnumbered assets with unclear relationships. Do not replace tags literally intended for speech within lines. Video tags retain references supplied by the user or host; invent no standardized video tag.

A timbre reference and source audio for extraction have different responsibilities. Preserve existing mappings; ask first when multiple assets and characters cannot be paired. A prompt does not substitute for actually supplying assets.

## Timeline form

Use the sources' `[start s:end s]` notation without spaces inside numerical labels, e.g. `[6.3s:10.5s]`. State an established total duration at the start. Continuous music/ambience may span multiple lines. Put intervals beside their sound objects; define roles in advance if useful.

Literal Chinese prompt example:

> 总时长 12 秒。阿宁的声音清亮，表达从迟疑转为坚定。
> [0.0s:12.0s] 轻柔钢琴在背景铺底，对白时保持较轻，最后两秒渐弱。
> [2.0s:5.0s] 阿宁说：“我会回来。”
> [4.5s:6.0s] 在台词末尾响起轻微衣料声，随后脚步渐远。

This example is not a universal default. Use time labels only when users supplied the corresponding time points/intervals or explicitly authorized timeline design. Total duration does not authorize segmentation. With total duration only, use natural-language total duration and event order, adding neither segment nor whole-piece labels. Local timing does not authorize filling other segments; relative pauses do not become calculated absolute intervals. Endpoints must not exceed an explicit total duration or this edition's 360-second ceiling. Preserve purposeful cross-layer overlap; resolve unreasonable simultaneous repeated speech by the same character. Interval start must precede end. Clarify conflicting user values rather than changing times yourself.

Special separators in isolated source examples have not been confirmed as mandatory syntax. Timestamps are creative control, not guarantees of frame/sample precision or fixed timing tolerance.

## Track-extraction form

Literal Chinese instruction example: “从 @音频1 中单独提取女生的人声，输出一条独立人声轨。”

Multiple-target literal example: “从 @音频1 中分别提取小周的人声、小林的人声和背景音乐，输出三条独立音轨。” Use only when users have established those characters and content; never assume those sources exist in the source audio.

Extraction preserves original vocal lines, order, and performance; do not convert it to newly written dialogue or redubbing. Do not add voice conversion, editing, or content changes unless requested. Output extraction instructions only; never claim track files have been completed.

## Audio reference with bare dialogue

When one known timbre reference is bound to input dialogue, use the standard tag plus current-line performance and original line. Literal Chinese template: “说话者（@音频1）以诚恳的语气、自然语速说：‘原台词’”. Do not infer age, gender, identity, personality, or voice characteristics from text, or claim audio analysis. Invent no reference without existence/tag information. See the shared sound guide for details. These examples do not authorize translating input-language output.

## User-supplied delivery conditions

Do not proactively add sample rate, encoding, channels, etc., or promise they are guaranteed by prompts alone. Preserve explicit requests as short natural-language delivery conditions, e.g. “Delivery requirement: 48kHz, implemented through generation or export configuration,” in the input-appropriate language. Do not include request fields or calling steps. Clarify first if current rules explicitly do not support the condition and the user disallows alternatives; unknown configuration details do not justify deleting it. Independent track count and generation/extraction method remain subject to this edition's capability boundaries.
