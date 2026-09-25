# YMM4 NEWS60 reusable production routine

This is the repeatable editor setup for the scoped 55–60 second Zundamon news profile. The canonical policy remains `config/zundamon_news60_template.json`; the machine-readable routine is `config/ymm4_news60_routine.json`. The preparation command creates an import package, not a `.ymmp`, WAV, or MP4.

## Build once on a supported Windows machine

1. In YMM4, make a 1080×1920, 30 fps project named `ZUNDAMON_NEWS60_BASE.ymmp`. Keep the baseline local and duplicate it for each episode. Add stable background and safe-zone guides, closing layout, empty visual/BGM/effect slots, and room for source credits. Keep guides out of the final export.
2. Configure Zundamon and optional Shikoku Metan as characters with local VOICEVOX voices, green (`#4DE084`) and pink (`#FF5BB9`) normal caption styles, and separate character settings. Save these in the baseline. Do not enable automatic keyword colors. If VOICEVOX WAVs are made separately, use VOICEVOX voice presets and its pronunciation dictionary; verify settings separately for YMM4's direct VOICEVOX integration.
3. Create four reusable item templates in YMM4 by selecting the finished item(s), right-clicking and registering each as a template: `NEWS60/01_HOOK_TITLE`, `NEWS60/02_EXPLAINER`, `NEWS60/03_EVIDENCE_FRAME`, `NEWS60/04_TAKEAWAY`. The official editor supports item-template registration and opening templates with Ctrl+T. Keep each layout readable on a phone. Use one main visual focus per beat; do not animate everything at once.
4. Register a short character/nameplate treatment as an additional item on each character only if it helps. YMM4 supports linking a registered item template to a character's additional items. The template must not cover captions or claim-bearing evidence.
5. Configure a compatible moving/PSD character asset and test VOICEVOX mouth data on the actual full face. A cached shell or reaction pack made for another renderer is not proof of YMM4 compatibility. Record the asset identity, rights/credit, mouth alignment and two representative expression states. Recheck when the character asset or geometry changes.
6. Import a tiny harmless CSV and confirm exact speaker names, voice generation, caption styling, mouth motion and source-credit position in YMM4. Save the checked baseline. This Windows check remains outstanding until it is actually performed.

Do not commit third-party images, character layers, music or the local `.ymmp` containing them to the repository. Keep a verified, rights-aware cache and recheck publication rights when used.

## One script, one import package per episode

Copy `examples/ymm4_news60_script_template.json` to a new episode file. Replace every `null` title and text, add real `source_claim_ids` and visual cues, and delete or expand beat rows as the story warrants. The second speaker is optional. A null field intentionally blocks preparation.

```bash
python scripts/prepare_ymm4_news60_routine.py \
  --input path/to/locked_script.json \
  --output-dir path/to/new_episode_ymm4_inputs
```

The command writes a new directory with `ymm4_script.csv`, `ymm4_review_cues.json`, `ymm4_beat_sheet.json`, `ymm4_run_sheet.md`, and `ymm4_package_manifest.json`. It refuses to replace an existing directory. The manifest records input, policy, blueprint and package hashes. The beat windows are editorial guides; they are never substituted for measured VOICEVOX timing. Missing beats, factual lines without claim IDs, and visual cues without claim IDs are marked for review, not silently declared acceptable.

Duplicate the checked `.ymmp` baseline, import the two-column CSV with YMM4's built-in script importer, and use the beat sheet for layout slots. The importer does **not** apply the sidecar's captions, emotions, visuals, colors or source claims. Review `caption_matches_voice_text: false` rows and put the approved `caption_text` on screen without changing the approved audio. YMM4 can separate a voice item's subtitle into a text item. Check for duplicate subtitles and full spoken-text coverage.

Apply only the explicit non-normal expression cues where they aid the meaning. Reuse the character mouth and layout setup. Fill visual slots from the verified cache where the image actually supports the claim; otherwise research and verify a fitting source. Measure the resulting local audio, align captions, preview mouth/face/safe zones, then perform the existing render and final review gates when video production is authorized.

## Working time target

For a warm project with source material already available, a verified Windows baseline and matching rights-cleared cached assets, try this **about-ten-minute target** and log actual elapsed time from task start:

| Target interval | Work |
|---|---|
| 0–3 min | Verify claims, lock the short script and choose only necessary visuals |
| 3–5 min | Prepare the import package; start independent voice/asset/toolchain preparation after the lock |
| 5–8 min | Import into the duplicate project and adjust changed visuals, captions and expression cues |
| 8–10 min | Preview, render once, decode and watch representative output |

These intervals are a hypothesis, not measured performance or a deadline. New evidence, missing rights, a cold character setup, a failed mouth fixture, or a slow encode can extend the run. Record stage times, cache hits, revision count and final QA outcome before claiming an improvement over the observed roughly 40-minute baseline. Keep the 10–15 minute policy goal as a goal, not a guarantee.

## Source of the reusable editor techniques

- YMM4 item/character additional-item templates: https://manjubox.net/ymm4/faq/%E3%82%86%E3%81%A3%E3%81%8F%E3%82%8A%E3%83%9C%E3%82%A4%E3%82%B9/%E5%AD%97%E5%B9%95%E3%81%A8%E4%B8%80%E7%B7%92%E3%81%AB%E3%82%AD%E3%83%A3%E3%83%A9%E3%82%AF%E3%82%BF%E3%83%BC%E3%81%AE%E5%90%8D%E5%89%8D%E3%82%84%E8%83%8C%E6%99%AF%E3%82%92%E8%A1%A8%E7%A4%BA%E3%81%97%E3%81%9F%E3%81%84/
- YMM4 template open/duplicate and search tools: https://manjubox.net/ymm4/release/4.11.0.0/
- YMM4 two-column script import: https://manjubox.net/ymm4/faq/editing/%E5%8F%B0%E6%9C%AC%E3%83%95%E3%82%A1%E3%82%A4%E3%83%AB%E3%82%92%E3%82%82%E3%81%A8%E3%81%AB%E3%83%9C%E3%82%A4%E3%82%B9%E3%82%A2%E3%82%A4%E3%83%86%E3%83%A0%E3%82%92%E8%BF%BD%E5%8A%A0%E3%81%99%E3%82%8B/
- VOICEVOX presets and phoneme timing: https://voicevox.hiroshiba.jp/how_to_use/
- YMM4 compatible VOICEVOX lip sync: https://manjubox.net/ymm4/release/4.49.0.0/
- YMM4 subtitle separation: https://manjubox.net/ymm4/release/4.16.0.0/
