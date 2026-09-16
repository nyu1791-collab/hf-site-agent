# Evidence Visual + Static Character Standard

Effective: 2026-09-16 JST
Status: mandatory media standard

## 1. Background and evidence visuals

For news, factual explainers, product updates, software updates, longform explainers, and other claim-bearing videos, the default visual path is search first, not image generation.

Use this priority order:

1. Official or primary source visual.
2. Official newsroom, press kit, support page, product page, release notes, or official documentation visual.
3. Rights-cleared screenshot or excerpt from the primary document when it directly supports the claim.
4. Rights-cleared real photo or media asset.
5. Wikimedia Commons or an equivalent source with a verified reusable license.
6. Trusted secondary sources only for discovery when the original source or rights state still needs verification.

Every evidence-bearing visual must retain its source page, asset locator, a short source description, the scene or claim it supports, and its reuse/license state.

Search results are discovery, not permission. Unknown-rights or all-rights-reserved imagery must not be placed into a public output unless permission or another valid basis for reuse is established. Internal review use does not create publication rights.

AI-generated background images are not the normal path for factual or news videos. Generated illustrations may be used only when the user explicitly requests them or when the visual has no evidentiary role. They must never be presented as proof of a factual claim.

Verified source assets should be cached with provenance and reused when they still fit the scene. Do not redownload the same verified asset without reason.

## 2. Zundamon and Shikoku Metan

The default visible behavior is **STATIC_TURN_FOCUS**.

Normal dialogue does not use mouth animation, lip sync, blinking, head tilt, pose changes, body bobbing, speech-start bounce, reaction-symbol animation, repeated entry/exit movement, or continuous character zoom/pan.

The only required turn-state change is speaker focus:

- Active speaker: scale **1.08**, opacity **100%**, visually in front.
- Inactive listener: scale **1.00**, opacity **55%** by default. The accepted half-transparent range is **50–60%**.
- Switch the two states at the actual voice-turn boundary.
- Do not interpolate or animate between those focus states by default; use a direct state switch.

If the evidence visual is the primary information carrier, both characters may be reduced or temporarily hidden so they do not obstruct captions or evidence.

## 3. Precedence

This standard overrides older defaults that required character mouth animation, blink animation, frequent expression changes, speech-start bounce, or other state-driven character motion. Older media policies remain valid only for non-conflicting rules such as captions, audio timing, rights, factual verification, safe zones, encoding, and QA.

A later explicit user instruction may override the static-character presentation, but rights, factual, safety, and cost controls remain mandatory unless the governing policy permits otherwise.

## 4. Production speed

For ordinary static-turn-focus production:

- Do not rebuild or regenerate a character reaction pack.
- Do not run full-face mouth-state fixtures for unchanged static portraits.
- Do not regenerate existing VOICEVOX audio or timing merely because source imagery or speaker-focus presentation changed.
- Repair only the smallest affected visual layer.
- Search, rights verification, and asset materialization may overlap when they are independent, but rights and claim gates are never skipped for speed.

## 5. Required QA

Before delivery, verify representative turns for active-speaker scale, inactive-listener opacity, absence of unrequested character animation, evidence/source semantic match, source receipt completeness, safe-zone integrity, and public-use rights state when publication is intended.

A technical decode pass by itself is not a visual-quality pass.
