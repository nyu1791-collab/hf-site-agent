# Durable Media Automation — Small Coordinator, External Media Worker

For the single-VM 2 GiB layout that preserves the existing `runtime/media-queue.sqlite3`, see [GCP_SMALL_HOST_DEPLOYMENT.md](GCP_SMALL_HOST_DEPLOYMENT.md). That local setup is the primary fit when no separate Linux worker is available; the remote renderer below is optional.

## What this branch implements

- `scripts/media_source_daemon.py` polls the configured official RSS feed every 5 minutes using conditional requests, normalizes entries and deduplicates them into SQLite/WAL. Polling only creates inbox records; it does not start a render or publish.
- `scripts/media_news_pipeline.py process-next` handles at most one queued article per run: fetch the allowlisted article, make one exact-`:free` script request, synthesize narration, and download image candidates. The daily model-call cap, zero-cost response check, and no-paid-fallback rule remain active.
- VOICEVOX runs locally by default. An optional SSH reverse tunnel can move VOICEVOX inference to an already available computer while the coordinator calls only a loopback URL. The tunnel is optional and has not been connected to a live host in this change.
- Audio and timing are hash-checked and reused when their inputs are unchanged. A VOICEVOX failure retries after 60 seconds and 5 minutes, then moves the item to `VOICE_BLOCKED`. Once the connection is repaired, requeue that item with the `retry-voice` subcommand.
- Queue preparation pauses at the first voice failure or human rights-review boundary, and when workspace free disk falls below 2 GiB. It does not delete packages to recover space.
- The systemd preparation service stores per-line WAV cache data under `/var/lib/hf-site-agent/voice-cache`, outside the read-only repository checkout. Local deployments can set `VOICEVOX_CACHE_DIR` to another persistent writable directory.
- Downloaded images stay `REVIEW_REQUIRED`. A human must record the reuse basis, evidence URL and credit before render. A successful local render ends at `READY_TO_PUBLISH`; posting is not implemented.
- `scripts/media_render_worker.py` and `scripts/media_render_transport.py` implement a bounded render handoff. `render --remote-render` sends one reviewed package through a loopback-only SSH reverse tunnel, checks pinned code/shell/font hashes, and verifies the returned MP4 before advancing the queue.
- The source poller and preparation timer templates both use a 5-minute interval. The worker and tunnel systemd files are templates only. No VPS, worker credentials, SSH account, external worker or continuous uptime is configured or verified by this change; live status remains `IMPLEMENTED_NOT_CONNECTED`.

## Which work happens on which machine

The coordinator is the source of truth: it owns the SQLite/WAL queue, calls the configured model API, downloads article images, and prepares narration and timing. The external worker receives only that narration, timing, presentation data and selected rights-cleared images. It has no queue/database, provider credentials, repository write access, or publishing function. SQLite/WAL never crosses the network.

The render API binds only to `127.0.0.1:18765` on both machines. The worker opens an SSH reverse tunnel to the coordinator's same loopback port; a shared random bearer token adds an application-level gate inside SSH. The coordinator transfers files over that tunnel, not through GitHub Actions, a public bucket or a public HTTP endpoint. Input/output size, duration and time are bounded. The coordinator and worker verify the renderer, profile, media policy, approved character shell and font fingerprints.

There is no automatic render retry or local fallback. If the worker is unreachable or any hash/probe fails, the command stops and the source stays at `ASSET_REVIEW_REQUIRED`. A verified MP4 and report are written locally; publishing is still disabled. The worker must be an already available Linux machine with the approved shell/font, FFmpeg/ffprobe and Pillow. Measure its peak RAM, disk use and render duration before choosing any host size.

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

## Optional remote render worker

Install the same approved repository revision on an already available worker, along with FFmpeg/ffprobe and Pillow. Put the approved character shell outside the repository (for example, `/srv/hf-render-assets/approved-shell`) and the approved font at a stable, read-only path. The worker check reports shell, font, renderer, profile and handler SHA-256 values, even before asset hashes are pinned, and lists missing dependencies. It exits with status 2 while required pins are absent. Set the shell and font hashes in both protected environment files; the coordinator also compares all code hashes against its own checkout before accepting a job.

On the worker, configure `/etc/hf-render-worker/worker.env` with restrictive permissions (`root:root`, mode `0600`):

```text
MEDIA_RENDER_SHARED_TOKEN=<same random 32-byte token on both hosts>
MEDIA_RENDER_SHELL=/srv/hf-render-assets/approved-shell
MEDIA_RENDER_SHELL_SHA256=
MEDIA_RENDER_FONT=/srv/hf-render-assets/approved-font.ttf
MEDIA_RENDER_FONT_SHA256=
MEDIA_RENDER_WORK_DIR=/var/lib/hf-render-worker
```

Initially leave both hash values empty. After installing the worker units below, run the check and inspect its redacted JSON output. Copy the reported shell/font hashes into `worker.env`; rerun the check and require `"status":"READY"` before starting the listener. The check does not accept jobs or print the token.

On the coordinator, create `/etc/hf-site-agent/media-render.env` (`root:root`, mode `0600`) with the same token and approved asset hashes:

```text
MEDIA_RENDER_SHARED_TOKEN=<same random 32-byte token on both hosts>
MEDIA_RENDER_EXPECTED_SHELL_SHA256=<approved tree SHA-256>
MEDIA_RENDER_EXPECTED_FONT_SHA256=<approved font SHA-256>
MEDIA_RENDER_WORKER_URL=http://127.0.0.1:18765/v1/render
```

Generate the bearer token once with a cryptographically secure random generator and transfer it to the two root-protected environment files through an approved secret channel. Do not put it in this repository, command history, terminal output, or logs. Keep the SSH private key on the worker and provision the matching public key only for the restricted `hf-render-tunnel` account on the coordinator.

Restrict the coordinator SSH account to remote forwarding on `127.0.0.1:18765` only. For its `authorized_keys` entry, use `no-agent-forwarding,no-X11-forwarding,no-pty,no-user-rc,permitlisten="127.0.0.1:18765"`. In `sshd_config`, apply a `Match User hf-render-tunnel` block with `AllowTcpForwarding remote`, `AllowStreamLocalForwarding no`, `PermitListen 127.0.0.1:18765`, `GatewayPorts no`, `AllowAgentForwarding no`, `X11Forwarding no`, `PermitTTY no`, `PermitTunnel no`, and `MaxSessions 0`. Give the account no repository or service-management permissions. Verify the effective sshd configuration before connecting. Do not use the coordinator's normal login account.

Install the worker and tunnel unit templates from `deploy/systemd/` after preparing the protected environment and SSH host-key files. Create `/var/lib/hf-render-worker` and its `home` and `tmp` directories owned by `hf-render-worker`; install the pinned shell and font read-only under `/srv/hf-render-assets`. Set `/etc/hf-render-worker` to `root:hf-render-worker`, mode `0750`; keep `worker.env` and `tunnel.env` owned by root, mode `0600`; give `id_ed25519` to `hf-render-worker`, mode `0600`; and make the verified `known_hosts` file readable by that account. Create `tunnel.env` with `COORDINATOR_HOST=<verified coordinator hostname>`. Strict host-key checking is enabled; do not populate `known_hosts` from an unverified scan.

On the worker host, install and check the worker units:

```bash
sudo install -m 0644 deploy/systemd/hf-render-worker.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/hf-render-worker-check.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/hf-render-worker-tunnel.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start hf-render-worker-check.service
sudo journalctl -u hf-render-worker-check.service --no-pager -n 30
```

After copying the reported shell/font hashes into `worker.env`, require the next check to report `READY`, then start the listener and reverse tunnel:

```bash
sudo systemctl start hf-render-worker-check.service
sudo systemctl enable --now hf-render-worker.service hf-render-worker-tunnel.service
sudo systemctl is-active hf-render-worker.service hf-render-worker-tunnel.service
```

On the coordinator host, install its units and verify the authenticated tunnel health check:

```bash
sudo install -m 0644 deploy/systemd/hf-site-agent-media-render@.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/hf-site-agent-media-render-check.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start hf-site-agent-media-render-check.service
sudo journalctl -u hf-site-agent-media-render-check.service --no-pager -n 30
```

The coordinator health check does not render or touch the queue. Keep the render template disabled until a human has reviewed a specific package and explicitly starts it.

After manual rights review, start one render for that package from the coordinator:

```bash
sudo systemctl start 'hf-site-agent-media-render@<source_id>.service'
sudo journalctl -u 'hf-site-agent-media-render@<source_id>.service' --no-pager -n 50
```

Replace `<source_id>` with the package's 64-character lowercase SHA-256 ID. This command does not call an AI API. It sends the already prepared assets once, validates the returned MP4 with `ffprobe`, records `remote-render-report.json`, and stops at `READY_TO_PUBLISH`. If an attempt fails, diagnose it before manually running another attempt. Public posting remains out of scope.

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

The coordinator systemd examples assume a dedicated Linux account `hf-site-agent`, a checkout at `/opt/hf-site-agent`, and persistent storage at `/var/lib/hf-site-agent`. The remote worker template assumes a separate unprivileged account `hf-render-worker`. Install units only on the corresponding hosts and set restrictive permissions on all protected environment and SSH files. Do not enable public posting, automatic top-up or paid fallback.

```bash
sudo install -m 0644 deploy/systemd/hf-site-agent-media-source.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/hf-site-agent-media-news.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/hf-site-agent-media-news.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hf-site-agent-media-source.service hf-site-agent-media-news.timer
```

`durable_media_runner.py` remains the separate typed batch control plane with SQLite/WAL, dedupe, leases, bounded retry, path checks, child secret isolation and process-group cleanup. Passing CI proves repository behavior only; it does not prove a VPS has been provisioned or that any production workflow is running.
