# YMM4 research explainer profile

This profile captures the supplied recording's visual language and turns it into a reusable longform routine. The supplied file is a 42.16-second, 2732×2048 HEVC iPad screen recording with browser/player chrome. Its embedded player showed 19:06; neither duration nor capture geometry defines the finished video's length or canvas. The recording is a style reference only, and none of its factual claims or media are reused.

## Reference details and deliberate quality improvements

- Open with a short exchange, then keep a compact orange chapter label fixed in the upper left until the next chapter.
- Place Zundamon on the right and Shikoku Metan on the left. Keep the active speaker bright and more prominent; leave the listener visible but subdued. Use scale and opacity values only as starting points until a Windows preview confirms the characters remain readable.
- In the sampled scene, the center was a bright soft-focus background. A claim-matched source figure or diagram is a quality improvement, not a feature confirmed in the recording. Generic lab imagery is atmosphere only; never present it as evidence.
- The reference uses full-spoken captions across up to two lines in a broad rounded dark-blue lower band, white body text, speaker-colored name tabs, and a warm orange source band along the bottom. Keep the band large enough for full text, use semantic line breaks, and check phone-size readability. Preserve the existing preference against an oversized near-black panel.
- The source band should prioritize the source supporting the current scene, with its exact title, date when available, and source ID. Keep locators and claim-to-source support summaries in the sidecar.
- Reserve red emphasis for a few manually selected phrases. Keep voice-synchronized mouth motion and sparse meaning-led expression changes; avoid constant zoom.

## One-time YMM4 baseline and exported templates

Create and check the baseline on Windows. The recording is an iPad screen capture with player and browser chrome, so it does not establish the final video's canvas, resolution, or frame rate. Start with 1920×1080 at 30 fps only as a trial setting and compare against the actual master before locking it.

Use the installed YMM4 version and verify both required functions on that machine. YMM4 release notes document template import/export from v4.29 and voice-aware vowel mouth movement for compatible moving or PSD standing art from v4.49. A version number alone is not proof that the character assets work.

Register and export these seven reusable items once:

1. LONGFORM/01_HOOK_DIALOGUE — short opening exchange and speaker name tabs.
2. LONGFORM/02_CHAPTER_PILL — chapter label fixed during the chapter; update the text at chapter changes.
3. LONGFORM/03_DIALOGUE_EVIDENCE — characters at the sides, source-matched image or small diagram in the center when useful.
4. LONGFORM/04_MECHANISM_DIAGRAM — clear static diagram with a meaningful pointer or reveal.
5. LONGFORM/05_LIMIT_OR_COMPARISON — comparison or limitation card.
6. LONGFORM/06_SOURCE_STRIP — warm lower band with scene-specific source title, date, and ID.
7. LONGFORM/07_TAKEAWAY_END — resolved takeaway and references.

For every template, preview long Japanese dialogue and both characters, then export the template bundle, import it into a clean test profile, and preview it again. Keep the actual project, third-party character layers, and template exports local unless their redistribution rights are verified. The repository profile is not an exported YMM4 template.

Confirm the selected standing art supports VOICEVOX-aware mouth information. YMM4 4.49 documents this for compatible vowel-mouth assets; test both Zundamon and Shikoku Metan with the installed version and actual local assets. If an asset is incompatible, label the fallback accurately and do not claim phoneme-synchronized motion.

## Prepare a researched episode

Copy examples/ymm4_research_explainer_script_template.json. Fill the research cutoff date, exact source metadata, chapter outline, every dialogue line, and the pronunciation dictionary entries needed for terms whose spoken form differs from their display spelling. Keep distinct factual claims under distinct claim IDs; do not reuse C1 for unrelated statements. Give factual hooks, mechanisms, limitations, and takeaways their own source-linked claims too. The template's six chapter slots are examples and can be merged, split, or removed to fit the evidence.

A VERIFIED claim needs, for every cited source, an exact section/page/figure/time locator and a concise support summary, plus reviewer, review method, and review date. A QUALIFIED claim also needs the qualification itself. Use `PRIMARY_OFFICIAL`, `PRIMARY_RESEARCH`, `SECONDARY_REPUTABLE`, `DATASET_OR_METHOD`, or `OTHER` (with a note). These statuses are editor assertions; the package builder does not fetch sources or independently prove the claim. REVIEW_REQUIRED claims remain visibly flagged, and source/review dates cannot exceed the declared research cutoff.

Every selected visual asset has five provenance fields: source page, asset locator, license or public-domain state, line/scene/claim mapping, and retrieval or verification timestamp. `CLEARED` also requires the exact license/permission basis, a summary of the reuse conditions, review method, named reviewer, and review date. Block unknown, noncommercial-only, no-derivatives, and unverified editorial-only states. A generic source URL is not proof that a particular image is reusable. Voice terms, standing-art rights, and image rights are reviewed separately.

The preparation command is offline and creates CSV plus review sidecars:

python scripts/prepare_ymm4_research_explainer_package.py --input path/to/episode.json --output-dir path/to/new_episode_package

It blocks unknown IDs, invalid dates, unsupported evidence mappings, missing support for VERIFIED claims, unreviewed rights assertions, and captions that omit or alter spoken content. A pronunciation-dictionary substitution is allowed only when the resulting complete caption matches the spoken line after whitespace and Unicode normalization. A free-text reason cannot override missing words.

YMM4's CSV script importer takes character and spoken text in two columns. If one measured VOICEVOX WAV is available for each dialogue line, set each line's `voice_audio_file` to a unique `.wav` basename (no folder path) and run:

python scripts/prepare_ymm4_research_explainer_package.py --input path/to/episode.json --voice-audio-dir path/to/voice_wavs --output-dir path/to/new_episode_package

The optional UTF-8-with-BOM SRT contains full caption text timed from measured WAV frame counts, with the configured pause between dialogue lines. YMM4 can import an SRT as timed text items by dragging it to the timeline; it does not generate voice items from this SRT because cues have no voice prefix. Use the CSV for voice items and SRT for caption items. The package writes a SHA-256/frame-count/duration manifest, never copies the WAVs, and requires a Windows sample check for offsets, wrapping, line breaks, and collisions. It does not synthesize speech, create a YMM4 project, or render media.

## Voice, captions, timing, and reuse

Use VOICEVOX WAV output as the timing source rather than character count. For recurring foreign and technical terms, preserve the correct caption spelling through an explicit voice-reading-to-caption-spelling dictionary. Captions must cover every spoken word. Run a line-by-line spoken-audio comparison after importing timed caption items; inspect numbers, negation, names, technical terms, pauses, and captions for meaningful non-speech audio. The package validates dialogue coverage; the sound-event check remains part of the final human review.

The required credit strings for the standard cast are VOICEVOX:ずんだもん and VOICEVOX:四国めたん. Check the VOICEVOX software terms and each voice library's terms separately. The package includes a pending credit checklist; it does not claim that reuse terms have been reviewed.

## Ten-minute target and staged review

The ten-minute figure is an unmeasured warm-run target. Its clock starts after the source ledger, script, checked Windows baseline, voice settings, and rights-cleared cache are ready; it includes audio handling, assembly, render, machine QA, and visual review. Record wall-clock time for each stage in the generated run sheet and measure three consecutive clean warm runs before claiming success. Do not remove source, rights, caption, audio, decode, or visual checks to meet the target.

After a later production run is authorized, review in separate passes:

1. Evidence — verify every factual line against its exact source locator and distinguish reported findings from independent confirmation.
2. Captions — compare each full line with audio; check spelling, phone-size width, line breaks, kinsoku, and safe areas.
3. Voice — confirm both speakers, reading, prosody, measured timing, pause, and mouth-sync compatibility.
4. Visuals and rights — confirm line-to-asset mapping, semantic match, provenance fields, and reuse status.
5. Export — check ffprobe, complete decode, chapter order, representative frames, credits, and final playback.

## Official references

- YMM4 item templates and character additional items: https://manjubox.net/ymm4/faq/%E3%82%86%E3%81%A3%E3%81%8F%E3%82%8A%E3%83%9C%E3%82%A4%E3%82%B9/%E5%AD%97%E5%B9%95%E3%81%A8%E4%B8%80%E7%B7%92%E3%81%AB%E3%82%AD%E3%83%A3%E3%83%A9%E3%82%AF%E3%82%BF%E3%83%BC%E3%81%AE%E5%90%8D%E5%89%8D%E3%82%84%E8%83%8C%E6%99%AF%E3%82%92%E8%A1%A8%E7%A4%BA%E3%81%97%E3%81%9F%E3%81%84/
- YMM4 two-column script CSV: https://manjubox.net/ymm4/faq/editing/%E5%8F%B0%E6%9C%AC%E3%83%95%E3%82%A1%E3%82%A4%E3%83%AB%E3%82%92%E3%82%82%E3%81%A8%E3%81%AB%E3%83%9C%E3%82%A4%E3%82%B9%E3%82%A2%E3%82%A4%E3%83%86%E3%83%A0%E3%82%92%E8%BF%BD%E5%8A%A0%E3%81%99%E3%82%8B/
- YMM4 template import/export release: https://manjubox.net/ymm4/release/4.29.0.0/
- YMM4 VOICEVOX-aware vowel-mouth movement release: https://manjubox.net/ymm4/release/4.49.0.0/
- YMM4 timed SRT/SUB text-item import: https://manjubox.net/ymm4/faq/editing/item-from-subtitle-file/
- VOICEVOX WAV export and pronunciation control: https://voicevox.hiroshiba.jp/how_to_use/
- VOICEVOX software terms: https://voicevox.hiroshiba.jp/term/
- Zundamon and Shikoku Metan voice library terms: https://zunko.jp/con_ongen_kiyaku.html
- W3C captions: speech and meaningful non-speech audio, synchronized to the recording: https://www.w3.org/WAI/media/av/captions/
