# Evidence Visual + Static Character Standard

Effective: 2026-09-16 JST
Status: mandatory media standard

## 1. Background and evidence visuals

For news, factual explainers, product updates, software updates, longform explainers, and other claim-bearing videos, the default visual path is **real/primary visual first, not image generation**.

Use this priority order:

1. Official or primary source visual.
2. Official newsroom, press kit, support page, product page, release notes, or official documentation visual.
3. Rights-cleared screenshot or excerpt from the primary document when it directly supports the claim.
4. Rights-cleared real photo or media asset.
5. Wikimedia Commons or an equivalent source with a verified reusable license.
6. Trusted secondary sources only for discovery when the original source or rights state still needs verification.

When a rights-verified real photo, primary screenshot, or official visual already fits the claim, use it instead of generating a replacement. Do not invoke image generation merely to increase scene count or fill normal factual backgrounds. Verified real photos may be reused across semantically compatible scenes when the source attribution remains correct.

Every evidence-bearing visual must retain its source page, asset locator, a short source description, the scene or claim it supports, and its reuse/license state.

Search results are discovery, not permission. Unknown-rights or all-rights-reserved imagery must not be placed into a public output unless permission or another valid basis for reuse is established. Internal review use does not create publication rights.

AI-generated background images are not the normal path for factual or news videos. Generated illustrations are a last-resort non-evidentiary visual or an explicit-user-request path. They must never be presented as proof of a factual claim.

Verified source assets should be cached with provenance and reused when they still fit the scene. Do not redownload the same verified asset without reason.

## 2. Voice delivery and pacing

For the standard Zundamon + Shikoku Metan presentation, VOICEVOX uses **speedScale 1.20** by default.

- Generate the voice at 1.20 speed rather than speeding up an already rendered final track.
- Re-measure every generated WAV with ffprobe after the speed setting is applied.
- Caption timing and speaker-focus timing follow the measured audio, not estimated text length.
- If 1.20 harms intelligibility on a particular sentence, fix the smallest affected voice or wording layer rather than omitting captions or forcing an unnatural time stretch.
- Keep the information flow continuous. Do not add silence or filler merely to make a video longer.
- Default inter-turn pauses should stay at or below **0.35 seconds**; section-transition pauses should normally stay at or below **0.45 seconds**.
- Standard longform target is **6–12 minutes (360–720 seconds)** unless the subject itself justifies a different duration.
- Visual changes follow semantic beats and evidence changes, not decorative filler animation.

## 3. Captions and topic headings

Every spoken turn must be fully captioned. A fixed three-line cutoff, ellipsis caused by layout limits, or other silent omission of spoken text is forbidden.

The machine contract for new durable video runs is `FULL_SPOKEN_TEXT`: the timing manifest must carry the complete spoken turn (or a reviewed spelling-preserving equivalent), and a short visual headline may not replace it. The caption validator fails closed when coverage drops below the configured minimum or a caption record is missing.

Caption fitting order is:

1. semantic wrapping,
2. rendered-width wrapping,
3. reduce font size within readable bounds,
4. increase the caption panel inside its reserved safe zone.

If the full text still cannot fit, the render must fail closed for layout repair rather than truncate the caption.

Both the caption border **and the caption body text** identify the active speaker:

- **Zundamon:** bright green border + bright green caption text.
- **Shikoku Metan:** bright pink / magenta border + bright pink / magenta caption text.
- A thin dark stroke or shadow may be used to preserve readability.
- White caption body text is no longer the default for Zundamon/Metan dialogue.
- Important terms explicitly marked by the script use the stable emphasis colors yellow (`#FFEB3B`) or red (`#F44336`). Speaker color, speaker label and caption wording remain visible so color is never the only meaning channel.

Captions occupy their own reserved safe zone and must not overlap character art, evidence imagery, source attribution, or other captions.

A short heading appears above the caption area, but **the heading is content-based, not utterance-based**. Keep the same heading while the same subject, mechanism, argument, or evidence block continues. Change it only when the semantic content block changes. Do not generate a new heading merely because the speaker changed or a new utterance started.

Heading source priority is:

1. explicit `topic_heading`,
2. explicit `section_heading`,
3. scene title as fallback.

The heading should summarize the current content block and remain visually distinct from the full spoken caption.

## 4. Zundamon and Shikoku Metan

The default visible behavior is **STATIC_TURN_FOCUS**.

Normal dialogue does not use mouth animation, lip sync, blinking, head tilt, pose changes, body bobbing, speech-start bounce, reaction-symbol animation, repeated entry/exit movement, or continuous character zoom/pan.

The only required turn-state change is speaker focus:

- Active speaker: scale **1.08**, opacity **100%**, visually in front.
- Inactive listener: scale **1.00**, opacity **55%** by default. The accepted half-transparent range is **50–60%**.
- Switch the two states at the actual voice-turn boundary.
- Do not interpolate or animate between those focus states by default; use a direct state switch.

The caption safe zone takes priority over character size. Neither active nor inactive character may cover the caption panel. If the evidence visual is the primary information carrier, both characters may be reduced or temporarily hidden so they do not obstruct captions or evidence.

## 5. Precedence

This standard overrides older defaults that required character mouth animation, blink animation, frequent expression changes, speech-start bounce, fixed-line caption truncation, white-only caption body text, per-utterance headings, generated factual backgrounds, or slower default VOICEVOX delivery. Older media policies remain valid only for non-conflicting rules such as rights, factual verification, source provenance, encoding, and QA.

A later explicit user instruction may override presentation choices, but rights, factual, safety, and cost controls remain mandatory unless the governing policy permits otherwise.

## 6. Production speed

For ordinary static-turn-focus production:

- Do not rebuild or regenerate a character reaction pack just to animate normal dialogue.
- Do not run full-face mouth-state fixtures for unchanged static portraits.
- Do not regenerate existing VOICEVOX audio or timing merely because source imagery, captions, or speaker-focus presentation changed, unless the requested change is specifically voice speed or wording.
- Do not wait for image generation when a suitable real photo or primary visual already exists.
- Repair only the smallest affected layer.
- Search, rights verification, and asset materialization may overlap when they are independent, but rights and claim gates are never skipped for speed.

The canonical static speaker-color renderer for this presentation path is `scripts/render_static_speaker_color_longform.py`.

## 7. Required QA

Before delivery, verify representative turns for:

- full caption coverage with no fixed-line truncation,
- semantic topic-heading granularity rather than per-utterance heading churn,
- correct green/pink speaker border,
- correct green/pink caption body text matching the active speaker,
- no caption/character overlap,
- 1.20 VOICEVOX speed in measured timing metadata,
- active-speaker scale and 100% opacity,
- inactive-listener half transparency,
- absence of unrequested mouth, blink, pose, bounce, or character motion,
- evidence/source semantic match and source receipt completeness,
- real-photo/primary-visual preference over generated factual imagery,
- 6–12 minute longform target and no deliberate padding,
- public-use rights state when publication is intended.

A technical decode pass by itself is not a visual-quality pass.
