# Approved character video baseline

Read this file and `config/approved_video_template.json` before video work on every new tab. The approved presentation is the default for continued video tasks, short and long alike.

Preserve visible Zundamon and Shikoku Metan, native mouth and authored expression variants, bright background, pale speaker-specific subtitles and approved canvas. Speed 80% / quality 20% means skip optional polish, not remove the approved foundation. A static-card-only or different-canvas output requires an explicit user format request.

Reuse unchanged measured narration and timing for visual repairs. Reuse native character variants, fonts, layout and cleared topic-matched visuals. `scripts/render_reusable_short.py` supports one continuous long-form encode; `scripts/render_reusable_longform.py` is available for chapter recovery. Source visuals may supply a concise per-topic `title` without changing the template geometry.

One successful export, nonempty output and audio/video streams are sufficient for immediate delivery. No routine preview, full decode, agent review or cosmetic rerender. Missing requested characters or baseline is a functional defect requiring repair. Keep video runtime separate from the five-minute creation target. Image generation remains disabled. Publication, paid execution, merge, deploy and secret boundaries remain unchanged.


関連画像を多く使う構成・実際の二人の会話・平易な解説を制作の必須入力とする。各主要章に関連写真、製品画面、スクリーンショット、説明に役立つ図解を2〜4場面使う。公式素材は優先候補であり必須の限定条件ではない。自作の説明図も使えるが、文字だけのカードや同じ画像の使い回しは画像数に含めない。生成画像は禁止。素材の出典と利用根拠を保持する。ずんだもんと四国めたんは双方が実際に発話し、説明と視聴者の質問・言い換えを分担する。キャラクター表示だけで二話者とは扱わない。見出し、要点、平易な説明、必要なら短い具体例の順で説明し、モデル名・数値・専門語の羅列を避ける。新タブでも速度ポリシーの dialogue_contract / visual_density_contract / script_clarity_contract を必読する。入力は `scripts/validate_video_content_contract.py` で書き出し前に一度確認し、任意の査読や磨き込みは増やさない。


## 2026-10-05 Landscape longform presentation update

For explanatory/news **longform**, the default is now **16:9 landscape** using `config/approved_landscape_video_template.json`. The portrait template remains for Shorts/Reels/TikTok or an explicit portrait request.

The user-provided reference video is a **mood, pacing and information-hierarchy reference**, not a frame-for-frame design source. Preserve the useful feel: friendly modern AI-news presentation, two-character hosting, a large central evidence stage, clear top topic header, wide lower-third dialogue, source footer, chapter/title/evidence/summary scene variety, and frequent but controlled visual changes.

**Do not add morning-show styling.** No morning clock, sunrise/breakfast motifs, cheerful TV-morning gimmicks, or reference-specific cream/orange set dressing. Do not copy its background art, exact palette, plants/decorations, borders, title bars, logos or typography. The target is a modern AI briefing show with similar friendliness and rhythm but an original visual identity.

Longform characters stay visible without covering the evidence stage: Zundamon lower-left and Shikoku Metan lower-right by default, with native mouth movement, authored expressions and listener reactions. Visual-only redesigns must reuse unchanged measured narration/timing whenever possible to preserve the five-minute speed target.


## 2026-10-05 Gemini 3.8 Flash video-director integration

Gemini 3.8 Flash is a standard production participant for current/YouTube-relevant videos when Google Cloud ADC is available. Before VOICEVOX/rendering, use `scripts/gemini_video_director.py` to analyze relevant official/primary YouTube videos one URL per request and produce a structured research package.

Gemini owns: important timestamps/demos, 3–5 takeaways, plain-language examples, material limits, Zundamon/Metan editorial beats, and source-video/screen suggestions. Feed that package into the mission/script and presentation plan. Cache successful analysis.

Gemini does not replace source verification, rights clearance, VOICEVOX, FFmpeg, or publication approval. YouTube timestamps are editorial navigation only. If Gemini is unavailable and material facts are already verified elsewhere, continue the normal fast path rather than blocking the render.

Machine authority: `config/gemini_video_director_policy.json`. Human guide: `docs/GEMINI_VIDEO_DIRECTOR.md`.

## Gemini Video Director

Gemini 3.8 Flash is part of video **creation**, not merely review. When the subject is current, has relevant official YouTube material, or benefits from a keynote/demo, run the Gemini Video Research & Editorial Director stage before mission/script lock when runtime access is available. It analyzes one YouTube URL per request, extracts important timestamps/demos, proposes 3–5 takeaways and Zundamon/Metan dialogue beats, and maps lines to useful source visuals. The structured package then feeds the normal mission and presentation path.

Authority: `config/gemini_video_director_policy.json`  
Runtime: `scripts/gemini_video_director.py`  
Guide: `docs/GEMINI_VIDEO_DIRECTOR.md`

Gemini does **not** replace VOICEVOX, rights gates, the shared landscape renderer, FFmpeg, or final machine checks. YouTube analysis is not reuse permission. Cache successful research packages and do not rerun them without a source/topic/model/prompt-version change.

## Gemini video director

For current or YouTube-relevant explainers, Gemini 3.8 Flash is part of the production team as the Research & Editorial Director when Google Cloud ADC is available. Use it before mission/script lock to inspect useful official/primary YouTube videos, extract timestamps/demos/visual beats, produce 3–5 takeaways, propose plain examples/limits, and map Zundamon/Metan dialogue beats to visual evidence. Persist the result as a structured research package and feed it into the normal mission/presentation pipeline.

Do not use Gemini as an extra review round. It is a production stage. One YouTube URL is analyzed per request; multiple URLs are handled sequentially and merged. VOICEVOX, captions, renderer, FFmpeg and artifact checks remain deterministic. Gemini failure is non-blocking when current material facts are already verified through other primary sources.

## 2026-10-05 Speed engineering + measured review fixes

Read `docs/VIDEO_SPEED_ENGINEERING_RULES.md` before routine video execution. The fast path is **sequential by default**. Use at most two preparation lanes, and only for genuinely independent preparation or I/O. Shared VOICEVOX, FFmpeg encoding, shared cache writers and final artifact writers stay single-lane. More workers are not treated as faster by default.

Run Python compile + mission/template JSON preflight **before** apt, Docker, VOICEVOX startup, asset download or render. A syntax or manifest bug must fail in seconds. Reuse a healthy local VOICEVOX engine, verified character shell, cached WAV/timing, cleared visuals, fonts and character variants. Do not upgrade pip on the video critical path; install only missing dependencies.

Routine landscape video uses one final H.264 encode. Per-scene H.264 encode followed by concat is prohibited on the fast path; encoded scene chunks are recovery-only.

Measured review of `Gemini4_Argon_1min_landscape.mp4` produced permanent fixes:
- keep subtitle text fully inside the lower-third safe area, max 3 lines, >=34 px bottom margin, shrink font before clipping;
- the test measured about -24.47 LUFS, so fast VOICEVOX output should target approximately -18 to -16 LUFS using fixed gain + true-peak limiter **inside the single final encode**, not a second loudness render pass;
- for current claim-bearing videos, include at least one official/primary-source screenshot when available and provenance/rights gates pass; original diagrams explain but do not impersonate evidence;
- avoid holding identical composition as filler; change crop/zoom/evidence at semantic beats without adding review/rerender loops.
