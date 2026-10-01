# Durable Media Automation — Small Coordinator, External Voice Worker

## What this branch implements

- `scripts/media_source_daemon.py` polls the configured official RSS feed every 5 minutes using conditional requests, normalizes entries and deduplicates them into SQLite/WAL. Polling only creates inbox records; it does not start a render or publish.
- `scripts/media_news_pipeline.py process-next` handles at most one queued article per run: fetch the allowlisted article, make one exact-`:free` script request, synthesize narration, and download image candidates. The daily model-call cap, zero-cost response check, and no-paid-fallback rule remain active.
- VOICEVOX runs locally by default. An optional SSH reverse tunnel can move VOICEVOX inference to an already available computer while the coordinator calls only a loopback URL. The tunnel is optional and has not been connected to a live host in this change.
- Audio and timing are hash-checked and reused when their inputs are unchanged. A VOICEVOX failure retries after 60 seconds and 5 minutes, then moves the item to `VOICE_BLOCKED`. Once the connection is repaired, requeue that item with the `retry-voice` subcommand.
- The systemd preparation service stores per-line WAV cache data under `/var/lib/hf-site-agent/voice-cache`, outside the read-only repository checkout. Local deployments can set `VOICEVOX_CACHE_DIR` to another persistent writable directory.
- Downloaded images stay `REVIEW_REQUIRED`. A human must record the reuse basis, evidence URL and credit before render. A successful local render ends at `READY_TO_PUBLISH`; posting is not implemented.
- The source poller and preparation timer templates both use a 5-minute interval. They are repository files only. No VPS, daemon, credentials, external worker or continuous uptime is configured or verified by this change.

## Which work happens on which machine

The VPS is the coordinator: it owns the queue and source inbox, calls the configured model API, downloads article/images, runs the VOICEVOX client and assembles audio. With remote voice enabled, only the VOICEVOX HTTP inference runs on the worker computer. Audio transfer, FFmpeg conversion and final rendering still use the VPS. This is a useful small-host split, not a complete remote-render system.

The repository has not measured peak memory or render time on a VPS, so it cannot yet certify the cheapest instance size. Run one authorized local render and measure peak RAM and disk use before choosing the smallest plan.

Do not share the SQLite/WAL database over a network filesystem. A future full render worker needs an authenticated job/artifact handoff, content hashes and bounded result transfer; that transport is not part of this implementation.

## Optional remote VOICEVOX setup

The worker computer must already be running a VOICEVOX Engine HTTP server on its own loopback interface, normally `127.0.0.1:50021`. From that computer, open an SSH reverse tunnel to the VPS:

```bash
ssh -NT \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -R 127.0.0.1:50021:127.0.0.1:50021 \
  <dedicated-ssh-user>@<vps-host>
```

Configure the preparation service's protected environment file with:

```text
VOICEVOX_REMOTE_TUNNEL=1
VOICEVOX_URL=http://127.0.0.1:50021
```

The tunnel listens only on the VPS loopback interface. The wrapper rejects non-loopback remote URLs, checks `/version` and requires both standard speakers before starting synthesis. Do not expose port 50021 publicly. Use a dedicated SSH account/key with remote forwarding limited to this loopback port. If the tunnel is down, the job stays queued and uses the bounded retry schedule.

If voice retries are exhausted, repair/reconnect the engine and run:

```bash
python -m scripts.media_news_pipeline \
  --db <queue.db> --workspace <workspace> \
  retry-voice --source-id <source_id>
```

The command only returns the saved item to `VOICE_PENDING`; it does not call an AI API or publish anything.

## Staging commands

Run from the repository root with Python and FFmpeg installed:

```bash
python -m scripts.durable_media_runner --db runtime/media-queue.sqlite3 --workspace . init
python -m scripts.media_source_daemon --db runtime/media-queue.sqlite3 --once
python -m scripts.media_source_ingress --db runtime/media-queue.sqlite3 status
```

To process one article, configure `OPENROUTER_API_KEY` in the host's protected environment and either configure local VOICEVOX or keep the remote tunnel connected:

```bash
python -m scripts.media_news_pipeline --db runtime/media-queue.sqlite3 --workspace runtime process-next
```

This produces a saved mission, narration audio/timing and article image candidates, then stops for image-rights review. After each selected image has a verified reuse basis, HTTPS evidence URL and credit, render with the approved character shell and font:

```bash
python -m scripts.media_news_pipeline --db runtime/media-queue.sqlite3 --workspace runtime render \
  --package runtime/media-news/<source_id> --shell <approved-character-shell> --font <approved-font>
```

The output is local and remains at `READY_TO_PUBLISH`.

## Host installation (not performed)

The systemd examples assume a dedicated Linux account `hf-site-agent`, a checkout at `/opt/hf-site-agent`, and persistent storage at `/var/lib/hf-site-agent`. Install the service and timer files, set restrictive permissions on `/etc/hf-site-agent/media.env`, then verify the feed poller, queue, model API, VOICEVOX, one staged render and reboot recovery before unattended use. Do not enable public posting, automatic top-up or paid fallback.

```bash
sudo install -m 0644 deploy/systemd/hf-site-agent-media-source.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/hf-site-agent-media-news.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/hf-site-agent-media-news.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hf-site-agent-media-source.service hf-site-agent-media-news.timer
```

`durable_media_runner.py` remains the separate typed batch control plane with SQLite/WAL, dedupe, leases, bounded retry, path checks, child secret isolation and process-group cleanup. Passing CI proves repository behavior only; it does not prove a VPS has been provisioned or that any production workflow is running.
