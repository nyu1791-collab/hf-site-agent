# Gemini 3.8 Flash Video Director

**Status:** Permanent video-production role
**Provider:** Google Cloud Gemini Enterprise Agent Platform / Vertex AI
**Model:** gemini-3.8-flash
**Auth:** Application Default Credentials (ADC); never store an API key in the repository.

## Role

Gemini is a formal member of the video-production pipeline, not merely an optional reviewer. When runtime authentication is available and a current/YouTube-relevant video is being made, Gemini owns the Research & Editorial Director stage:

1. read relevant official or primary YouTube videos natively;
2. identify the most useful demonstrations, claims, visual moments, and timestamps;
3. reduce the topic to 3–5 takeaways;
4. propose plain-language examples and material limitations;
5. propose Zundamon/Metan question → answer → reaction beats;
6. map dialogue beats to specific source-video/screen moments;
7. emit a structured JSON research package that feeds the mission/script and presentation manifests.

The deterministic pipeline still owns VOICEVOX synthesis, measured timing, rights gating, cached assets, caption composition, FFmpeg, ffprobe, and final artifact checks.

## Why Gemini is a permanent video-production asset

Google Cloud supports direct YouTube-video input for Gemini video understanding. This makes Gemini especially useful for official keynotes, product demos, developer videos, long-form presentations, and visual evidence selection. A YouTube URL is evidence input; it is never permission to republish the underlying footage.

## One-URL rule

Use one YouTube URL per model request. If several videos matter, analyze them sequentially and merge only their structured results. Do not create a multi-agent swarm merely to fan out YouTube URLs. A second preparation lane is allowed only when the ordinary video speed policy independently admits it.

## Runtime

Set GOOGLE_CLOUD_PROJECT and GOOGLE_CLOUD_LOCATION=global, then run scripts/gemini_video_director.py with --topic, one or more --youtube-url arguments, and --output. Multiple videos may be supplied by repeating --youtube-url; the runtime sends one request per URL.

## Output contract

gemini-video-research-package-v1 contains per-source summary, 3–5 takeaways, important timestamps, visual beats, Zundamon/Metan script notes, material limits, and the source URL. The package feeds the normal mission/script and visual-source planning stages. Cache identity includes model, URL, topic, and prompt version.

## Evidence and rights

- Cross-check material current claims against current primary documentation when available.
- Generated summaries are not official quotations.
- Do not reproduce long transcripts.
- A timestamp is a navigation/editing reference, not reuse authorization.
- Public release remains behind the existing rights and approval gates.
- If Gemini/YouTube analysis is unavailable but material facts are verified elsewhere, continue the normal video pipeline rather than blocking delivery.

## Cost and safety

The route uses the user's authorized Google Cloud project and ADC. It never auto-tops-up, never prints credentials, never commits tokens/API keys, and never changes the PR/publication boundary.

## VM bootstrap

The persistent GCP VM must not install `google-genai` into the system Python. Use:

`scripts/install_gemini_video_director_runtime.sh`

It creates/reuses `$HOME/.venvs/hf-site-agent-gemini-video` and installs the tested SDK version without upgrading the system interpreter.

The VM service account needs `roles/aiplatform.user` on the Google Cloud project used for Gemini. Grant that role from an IAM-authorized user/admin context; the VM service account must not self-escalate. After IAM propagation, run the director with the venv Python.

Provider/IAM failures are sanitized by the runtime and must not print credentials, access tokens, or provider response bodies.


## Broad production authority

Gemini has broad authority **inside the video-production workspace**. It is not restricted to summarization or review. For each major editorial item, when relevant sources exist, Gemini should inspect multiple distinct YouTube videos before that item is locked.

Default breadth per major item:
- minimum 2 distinct videos when at least 2 useful sources are available;
- target 3 distinct videos;
- maximum 5 distinct videos;
- official vendor/developer/event channels first;
- deduplicate mirrors/reuploads of the same event.

Because the API accepts one YouTube URL per request, analyze each URL separately, cache the result, then have Gemini synthesize the item across those analyses. The synthesis must compare sources rather than concatenate them.

Gemini may decide and produce:
- source ranking and evidence notes;
- important timestamps, demos, UI screens, charts and visual moments;
- 3–5 takeaways;
- plain-language examples and material limitations;
- Zundamon/Metan question → answer → reaction structure;
- dialogue-direction draft;
- scene sequence;
- source-to-scene visual assignment;
- source manifest;
- presentation-manifest draft;
- pre-render editorial decisions that stay within approved video rules.

This is **production work**, not an extra review pass.

Gemini may write its structured artifacts into the video workspace/cache through the controlling runtime. It does not receive IAM administration, billing administration, secret-reading, repository merge/deploy, public-publish, VOICEVOX-synthesis, or FFmpeg-final-encode authority.

## Source-plan mode

For multi-item videos, prefer a source plan with major editorial items. Each item contains its own YouTube candidate URLs. The runtime analyzes up to 5 distinct videos for that item, then Gemini synthesizes them into one editorial package before mission/script lock.

The preferred target is 3 useful videos per major item. If only one relevant primary video exists, continue with one and record limited coverage rather than inventing extra sources.

The research package feeds:
- SOURCE_MANIFEST
- MISSION_SCRIPT
- DIALOGUE_DRAFT
- SCENE_PLAN
- VISUAL_SOURCE_PLAN
- PRESENTATION_MANIFEST

Do not rerun unchanged source analyses after downstream VOICEVOX/render/upload failures.
