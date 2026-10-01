# Durable Media Automation — Speed-First Staging

## What runs in this repository

- `scripts/media_source_daemon.py` polls only configured official RSS feeds and deduplicates items into the SQLite/WAL preparation inbox. It never calls an AI model, renders or publishes.
- `python -m scripts.media_source_ingress --db <queue.db> status` reports feed freshness, stale/error state, inbox counts and age of the oldest unprepared item. It reports the supervisor as unobserved because a database cannot prove the daemon process is alive.
- `scripts/media_news_pipeline.py` fetches an allowlisted official article, drafts a short Japanese Zundamon/Metan script in one current exact-`:free` OpenRouter call, synthesizes local VOICEVOX, and downloads article image candidates. A transactional daily cap is enforced; when reached, work stays queued for the next UTC day. Unknown/nonzero cost, malformed output or provider failure blocks that item; there is no paid fallback or automatic model retry.
- Images remain `REVIEW_REQUIRED`. Every image selected for render must have documented reuse rights and an attribution credit. Official-site presence alone is not a license. The renderer requires two distinct cleared images for every scene.
- The existing renderer writes `READY_TO_PUBLISH`. There is no public-posting code path here.
- systemd service and timer templates are under `deploy/systemd/`. They are examples committed to the branch; no VPS has been provisioned or enabled by this change.

## State boundary

`PREPARATION_REQUIRED` → `VOICE_PENDING` → `ASSET_REVIEW_REQUIRED` → (after rights review and manual render) `READY_TO_PUBLISH`

RSS scheduling and the script/voice/image-candidate implementation are present. Live 24-hour operation, VOICEVOX health on a host, actual provider credentials, an actual MP4 render, and production recovery have not been verified. The timer processes at most one inbox item per interval. Rights review and rendering are intentionally not automated.

## Staging commands

Run from the repository root with Python, FFmpeg/Pillow, and the existing VOICEVOX runtime installed:

```bash
python -m scripts.durable_media_runner --db runtime/media-queue.sqlite3 --workspace . init
python -m scripts.media_source_daemon --db runtime/media-queue.sqlite3 --once
```

To process one queued official article, configure `OPENROUTER_API_KEY` in the host's protected environment (never paste it into chat or commit it), set `VOICEVOX_ENGINE_DIR` for the local Engine, and run:

```bash
python -m scripts.media_news_pipeline --db runtime/media-queue.sqlite3 --workspace runtime process-next
```

This produces `runtime/media-news/<source_id>/mission.json`, `audio.wav`, `timing.json`, downloaded image candidates and VOICEVOX attribution. It stops for rights review. Edit `image-candidates.json` only after verifying reuse terms; set `selected_for_render: true`, `rights_verified: true`, a concise `rights_basis`, the HTTPS `rights_evidence_url`, and `credit` on the selected assets. The renderer rechecks the package-local image path and SHA-256, and rejects duplicate image contents. Select at least two distinct eligible images per scene. Then invoke:

```bash
python -m scripts.media_news_pipeline --db runtime/media-queue.sqlite3 --workspace runtime render \
  --package runtime/media-news/<source_id> --shell <approved-character-shell> --font <approved-font>
```

The render step uses the existing approved character and caption renderer. Its output remains local at `READY_TO_PUBLISH`.

## Host installation (not performed here)

The templates assume a dedicated Linux account `hf-site-agent`, a checkout at `/opt/hf-site-agent`, and persistent writable storage at `/var/lib/hf-site-agent`. Create `/etc/hf-site-agent/media.env` with restrictive permissions and the required provider/runtime variables, install the unit files, then enable the poller service and preparation timer. Keep the API key out of command-line arguments and logs. The preparation unit creates writable HOME/TMPDIR directories under `/var/lib/hf-site-agent`. First run in a staging directory and verify feed intake, one preparation item, local voice output, rights stop, render, and reboot recovery before considering unattended use.

```bash
sudo install -m 0644 deploy/systemd/hf-site-agent-media-source.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/hf-site-agent-media-news.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/hf-site-agent-media-news.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hf-site-agent-media-source.service hf-site-agent-media-news.timer
```

Do not enable public posting, auto top-up, or paid fallback. A successful render is not a publish authorization.

## Existing durable runner

`scripts/durable_media_runner.py` remains the typed `MEDIA_BATCH_RUN` control plane: SQLite/WAL queue, dedupe, leases, heartbeat, bounded retry, crash recovery, path validation, child secret isolation and process-group cleanup. It does not claim the news pipeline is deployed or that a host is running continuously.
