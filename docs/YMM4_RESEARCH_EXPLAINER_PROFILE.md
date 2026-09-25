# YMM4 research explainer profile

This profile turns the supplied screen recording into a reusable longform style and editing routine. The recording is a style reference only. The embedded player displayed 19:06; that does not change the project's usual 8–12 minute, content-led target, and none of the reference video's factual claims or media are reused.

## What to reuse from the reference

- Start with a short Zundamon and Shikoku Metan exchange, then move into the first chapter card.
- Keep a compact chapter label at the upper left. Place Zundamon on the right and Metan on the left so their dialogue frames a central source visual.
- Use a bright, softly blurred setting behind the characters. Use official or rights-verified source visuals in the center when they directly support a claim.
- Put dialogue in a compact dark rounded caption strip with white text and a dark outline. Keep it just large enough for the current line; avoid a tall near-black panel.
- Show speaker identity in a green Zundamon or pink Metan name tab. Reserve a small number of red highlights for manually selected phrases with a written reason.
- Keep a narrow source strip at the bottom. Show the exact source title and date when available, plus its [S1] style ID. A sidecar maps each ID to the claims it supports; a list of outlet names alone is not source tracing.
- Keep both characters visible when useful. Use synchronized mouth motion and sparse facial or pose changes that follow meaning. Balance their perceived sizes before speaker focus; do not add continuous zoom.

## One-time YMM4 baseline on Windows

YMM4 supports registered item templates, character additional items, and opening the template list with Ctrl+T. Its script CSV importer accepts two columns: character name and spoken line.

Create and check one local project named ZUNDAMON_METAN_RESEARCH_LONGFORM_BASE.ymmp. Start with a 1920×1080, 30 fps landscape project; compare its canvas to the original master when available. Keep the project and third-party character layers local, outside the repository.

Register these reusable items once:

1. LONGFORM/01_HOOK_DIALOGUE — short opening exchange and speaker name tabs.
2. LONGFORM/02_CHAPTER_PILL — chapter number and short question-style title.
3. LONGFORM/03_DIALOGUE_EVIDENCE — characters at the sides with a source image, quote, or small diagram in the center.
4. LONGFORM/04_MECHANISM_DIAGRAM — a clear static diagram with only a meaningful pointer or reveal.
5. LONGFORM/05_LIMIT_OR_COMPARISON — side-by-side evidence or a limitation card.
6. LONGFORM/06_SOURCE_STRIP — compact, scene-specific title/date/source IDs.
7. LONGFORM/07_TAKEAWAY_END — resolved takeaway and end references.

Save character settings, voice presets, safe areas, caption style, and empty media lanes in the baseline. Test each item on an actual YMM4 preview with both characters and long Japanese captions. Do not claim a valid .ymmp exists until this Windows check has passed.

## Make the next episode from the scaffold

Copy examples/ymm4_research_explainer_script_template.json. Fill the title, research date, source ledger, claim ledger, chapter titles, and every dialogue line. Keep one canonical script. Each factual line sets claim_bearing to true and references one or more claim IDs; each claim points to exact sources. Add a source before an unsupported factual line enters the script. Captions must cover the full spoken line; when caption spelling differs from voice text, fill caption_difference_reason so that exception gets reviewed.

Build the import package:

python scripts/prepare_ymm4_research_explainer_package.py --input path/to/episode.json --output-dir path/to/new_episode_package

The command creates a UTF-8-BOM CSV for YMM4 and review sidecars for captions, chapters, claims, sources, and package hashes. It blocks empty placeholders, duplicate IDs, unknown claim/source IDs, blocked claims, and caption differences without a written review reason. It marks source review, visual-use rights, audio timing, and editor QA honestly. It does not call the internet, synthesize speech, make a YMM4 project, or render audio/video.

Duplicate the checked baseline, import ymm4_script.csv, then use the chapter sheet to add reusable chapter and evidence items. The CSV imports only speaker and voice text; apply chapter titles, source strips, expressions, and caption_text from the sidecars. YMM4 direct VOICEVOX settings may not inherit presets from the standalone VOICEVOX app, so confirm the editor's actual settings.

Use VOICEVOX WAV output as the timing source, not script character count. Generate or reuse audio only after the source and script are locked. Align every caption to measured WAV timing, preserve full spoken coverage, check technical-term pronunciation, and retain compatible phoneme timing data for mouth sync.

## Ten-minute target and quality review

The ten-minute target is an unmeasured warm-run goal for an episode with locked evidence and script, a checked Windows baseline, ready VOICEVOX settings, and rights-cleared cached assets. It includes assembly, final render, machine checks, and visual rereview. Cold-start research, new rights work, or baseline setup can take longer. Record stage times and fix the repeated bottleneck; do not remove the rights, claim, caption, voice, decode, or visual checks to hit the number.

After a later production run is authorized, review the result in separate passes:

1. Claim pass — verify each statement against its source and distinguish a reported result from independent confirmation or consensus.
2. Readability pass — inspect phone-size captions, chapter labels, source strip, and character safe areas.
3. Audio pass — verify speaker, pronunciation, pauses, volume, and measured caption alignment.
4. Image and rights pass — confirm each visual fits the specific claim and its reuse status is clear.
5. Export pass — check ffprobe, complete decode, chapter order, representative frames, and final playback.

## Official workflow references

- YMM4 item templates and character additional items: https://manjubox.net/ymm4/faq/%E3%82%86%E3%81%A3%E3%81%8F%E3%82%8A%E3%83%9C%E3%82%A4%E3%82%B9/%E5%AD%97%E5%B9%95%E3%81%A8%E4%B8%80%E7%B7%92%E3%81%AB%E3%82%AD%E3%83%A3%E3%83%A9%E3%82%AF%E3%82%BF%E3%83%BC%E3%81%AE%E5%90%8D%E5%89%8D%E3%82%84%E8%83%8C%E6%99%AF%E3%82%92%E8%A1%A8%E7%A4%BA%E3%81%97%E3%81%9F%E3%81%84/
- YMM4 two-column script CSV: https://manjubox.net/ymm4/faq/editing/%E5%8F%B0%E6%9C%AC%E3%83%95%E3%82%A1%E3%82%A4%E3%83%AB%E3%82%92%E3%82%82%E3%81%A8%E3%81%AB%E3%83%9C%E3%82%A4%E3%82%B9%E3%82%A2%E3%82%A4%E3%83%86%E3%83%A0%E3%82%92%E8%BF%BD%E5%8A%A0%E3%81%99%E3%82%8B/
- YMM4 Ctrl+T template command and search/replace: https://manjubox.net/ymm4/release/4.11.0.0/
- VOICEVOX WAV export, presets, and phoneme timing: https://voicevox.hiroshiba.jp/how_to_use/
- W3C prerecorded captions: include dialogue, speaker identity, and meaningful non-speech audio: https://www.w3.org/WAI/WCAG20/Understanding/captions-prerecorded.html
- Official YouTube chapter timestamps if publishing is later authorized: https://support.google.com/youtube/answer/9884579?hl=en
