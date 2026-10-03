# GCP 24-hour AI coordinator

## Layout

Keep the existing VM, checkout, and SQLite database. The current host layout is `~/hf-site-agent/runtime/media-queue.sqlite3`; the pipeline workspace is `~/hf-site-agent/runtime`. Do not run `init` against a new database or copy the database to a network filesystem. The activation installer owns one lock-protected RSS poll timer and a separate preparation timer.

The GCP VM is the 24-hour coordinator and monitoring host. It owns the queue, starts the bounded agent/news preparation job, records checkpoints and results, and hosts the self-hosted GitHub Runner used by the private VM control plane. It is not the video-render machine. Language-model calls remain remote; VOICEVOX may remain local during transition or move through the verified private loopback tunnel, but final video rendering is handed to the separately provisioned external render worker. The SQLite pipeline lock prevents concurrent writers. No GPU or local always-on render worker belongs on this VM.

The initial target is `e2-small`: 2 GiB RAM with a shared-core sustained allowance of 0.5 vCPU. That is enough to try the sequential workflow, but it is CPU-limited and the five-minute creation target is not certified until actual job time and peak memory are measured. The user units cap each media job at 1,700 MiB so a failed job is stopped before it can consume the whole VM; a failed preparation/render remains recoverable from its saved checkpoint and does not publish. Preparation also pauses when one item is waiting for human rights review, and pauses below 2 GiB of free disk. It never deletes output automatically.

## Verify the existing VM before activation

The user identified instance-20261001-071545 as the existing Compute Engine VM; the supplied console screenshot showed it running as e2-small (2 GiB RAM). That screenshot is not live proof of current state. Re-open Compute Engine → VM instances and check the current project, VM name, zone, machine type, boot disk, service account, network, and power state. This deployment targets only that existing VM. Do not create a duplicate, stop or resize it, modify its disk, or reserve a static IP.

The user authorized approximately JPY 47,000 of Google Cloud credit as a hard ceiling. Before enabling the persistent timer, verify the live billing account, eligible remaining credit, expiry, project, region/SKU, and forecast in Google Cloud Console. Record a fresh, secret-free snapshot in ~/.config/hf-site-agent/cloud-budget.json with mode 0600; the activation preflight blocks if that evidence is missing, stale, belongs to another project, shows auto top-up, or forecasts more than the remaining credit. Google Cloud credit does not cover OpenRouter or any other third-party API bill. Vertex AI is not required or preferred.

Keep the current e2-small size unless live measurements justify an upgrade. The existing VM is already running; this process does not stop or resize it.

## Existing host bootstrap

The user units under deploy/systemd/user/ target the existing n_yu1791 home layout and preserve the existing database. GCP activation installs only the news preparation service and its five-minute timer; it does not install or enable any local GCP video-render unit. The user render template, when installed separately after rights review, is remote-worker-only. After the canonical branch reaches that VM, run the read-only preflight and then the authorized activation command as that Linux user:

```bash
cd ~/hf-site-agent
python3 -m scripts.install_gcp_small_host_services
python3 -m scripts.install_gcp_small_host_services --activate
```

Activation installs and enables a dedicated five-minute source timer, polls the configured official feeds once, and verifies fresh `OK` or `NOT_MODIFIED` observations before enabling the separate preparation timer. The source daemon's database lock prevents concurrent pollers from writing together. The preparation service only consumes the SQLite inbox and processes at most one item per five-minute run. Resident RSS script drafting first uses a catalog-verified free OpenRouter model, then may fall back only to the DeepSeek official API at `https://api.deepseek.com` using `DEEPSEEK_API_KEY` and `deepseek-flash`. DeepSeek has no artificial request, daily, or monthly spending cap on this route; provider balance and quota apply. Google Cloud credits are never treated as API credit, uncertain paid attempts are not retried, OpenRouter paid models/fallback and auto top-up remain disabled. If a prerequisite fails, the preparation timer stays disabled.

The worktree preflight blocks tracked edits and untracked files outside the explicit operational-artifact allowlist in `config/media_small_host_policy.json`. The allowlist covers `.media-cache/`, the persistent `runtime/` queue/workspace, and Python bytecode caches. The SQLite queue is still integrity-checked separately. Do not use `git clean` or delete runtime data to satisfy the source check; unknown untracked files and all tracked edits continue to block activation.

`media.env` is read from `~/.config/hf-site-agent/media.env`; keep it a regular file owned by the VM user with mode `0600` and never paste its secret into chat or command arguments. If its ownership, type, or mode is unsafe, activation blocks **without reading its contents**. Store a separate cloud-budget.json snapshot at ~/.config/hf-site-agent/cloud-budget.json with mode 0600; it contains billing facts only and no credentials. The activation script checks the protected media env, local VOICEVOX installation/version, free disk, existing queue integrity, canonical branch, GCP metadata identity, and recent billing evidence. It prints only blocker codes and service state. It never initializes the queue database, runs a paid API call, enables a render timer, or publishes.

Set the expected VOICEVOX version after verifying /version. Install the official Linux CPU Engine at ~/.local/share/voicevox_engine/linux-cpu-x64, or set VOICEVOX_ENGINE_DIR in the protected env file. The wrapper binds only to 127.0.0.1:50021, uses one CPU thread, and stops only an Engine process it started itself.

An existing always-available external computer may instead provide VOICEVOX through the private SSH reverse tunnel documented in DURABLE_MEDIA_AUTOMATION.md. Set VOICEVOX_REMOTE_TUNNEL=1 and VOICEVOX_URL=http://127.0.0.1:50021 in the protected media.env. In this mode the activation preflight probes the tunnel's /version and /speakers, requires the configured exact version and normal styles for both standard speakers, and does not require a local Engine directory. Public URLs, redirects, unavailable tunnels and a missing cast block activation. Moving inference does not itself free local disk; validate an actual synthesis before considering removal of an installed Engine. An intermittently available laptop or notebook is not a verified 24-hour worker.

Remaining cloud credit and authorized project spending are different quantities. A genuine balance above JPY 47,000 is valid; the next-30-day forecast must remain below both available credit and the JPY 47,000 authorization ceiling. Record actual current billing facts, never clamp or fabricate the account balance to satisfy preflight. This activation snapshot is not a live billing meter or an automatic spending-stop mechanism.

For cache diagnosis, include files as well as directories: du -ax -B1 --max-depth=1 ~/.cache. Directory-only du can report a large total while omitting downloaded archives at the cache root. Do not remove the installed VOICEVOX runtime, queue or voice cache as a diagnostic step. Identify and validate any specific regenerable download before a scoped cleanup; automatic artifact deletion remains disabled.

## Private VM-control completion gates

The private `nyu1791-collab/-hf-vm-control` workflow is the operational control plane for this existing VM. Its `update_test` operation compiles the coordinator/render modules and runs the news, source-ingress, GCP activation, small-host and remote-render regression suites before a fast-forward. Its `status` operation compares the local checkout with the current remote branch and emits separate coordinator, external-render, and saved-E2E readiness verdicts. A historical saved E2E result counts only when the queue row is `READY_TO_PUBLISH`, the non-symlink `final.mp4` is non-empty, its SHA-256 matches the `VERIFIED_REMOTE_RENDER` report, the report is fail-closed for publish/retry/fallback, and `ffprobe` proves both audio and video with matching duration/size metadata. The stronger `e2e_completion_verified` verdict additionally requires that saved report's renderer/profile/media-policy/render-policy/transport/worker/shell/font fingerprints to match the **currently authenticated connected worker**. An older successful MP4 therefore cannot certify the current stack.

After an actual VM reboot, run the private control-plane `reboot_audit` operation. It requires a new kernel boot ID relative to the activation baseline, lingering enabled, exactly one repository Runner service enabled and active, the five-minute user timer enabled and active, and a clean checkout at the current remote `ai-army/provider-v3` HEAD. The audit does not reboot the VM itself and does not publish or render.

To verify boot persistence for the installed PR head, the canonical Cloud Shell helper also provides `--operation coordinator-reboot-check`. It refuses to restart during an active render or media job, requires the paid-provider circuit to be paused, records a protected baseline for the exact current PR commit, resets only the existing coordinator VM, and waits for its Runner, both user timers, fresh RSS polling, authenticated worker health, and saved fixture jobs to recover. Queue rows and artifacts are checked for preservation. The user must run this operation in authenticated Cloud Shell because the self-hosted Runner does not have the required GCP reset permission.

External render readiness is never taken from a repository boolean. The authoritative check is the authenticated loopback health probe `python -m scripts.media_render_transport --check`; until that returns `READY`, the external renderer remains unavailable.

## External render handoff

The GCP coordinator stops after the source-backed script, narration/timing, image candidates, and review metadata are saved. It must not render the MP4 locally. After the selected assets have recorded rights evidence and credit, use the external render-worker path documented in [DURABLE_MEDIA_AUTOMATION.md](DURABLE_MEDIA_AUTOMATION.md).

Before dispatching a reviewed package, require the external worker health check to report `READY`, the SSH reverse tunnel to be configured, and the pinned renderer/profile/shell/font hashes to match. If the worker is unavailable, keep the queue item and checkpoint intact; do not fall back to local GCP rendering and do not publish.

For the current GCP user-service path, keep the renderer token and expected hashes in the single protected file `~/.config/hf-site-agent/media-render.env` (regular file, VM-user owned, mode `0600`). Do not duplicate this current-path secret into `/etc/hf-site-agent`. After the external worker and tunnel have been provisioned, install the **manual-only** user render/check units; neither has a timer:

```bash
mkdir -p ~/.config/systemd/user
install -m 0644 deploy/systemd/user/hf-site-agent-media-render-check.service ~/.config/systemd/user/
install -m 0644 deploy/systemd/user/hf-site-agent-media-render@.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user start hf-site-agent-media-render-check.service
systemctl --user status hf-site-agent-media-render-check.service --no-pager
```

The check is read-only and authenticated. A reviewed package is rendered only by an explicit `systemctl --user start 'hf-site-agent-media-render@<source_id>.service'`; these units are intentionally not enabled and cannot start from the five-minute preparation timer.

## Check the result

Use the existing database path for all checks:

```bash
python3 -m scripts.media_source_ingress --db runtime/media-queue.sqlite3 status
free -h
df -h /
systemctl --user list-timers --all
```

After one permitted preparation, record wall time and peak memory from the coordinator job's systemd cgroup/journal. Render timing and memory belong to the external worker and must be measured there. If coordinator preparation is OOM-killed or too slow, retain the queue and checkpoints and investigate before resizing. Do not add a GPU or local render workload to the coordinator.

## Billing note

Google's published `e2-small` compute price for `us-central1` is USD $0.016752855 per hour before disk, network, external IP, tax, and currency conversion. At 730 hours that is about USD $12.23 for VM compute alone. Check the selected VM's actual region and billing SKU before changing it. A Cloud Billing budget alert is a notification, not a general hard stop. Confirm the authorized project credit's current status and expiry before leaving the existing VM or timer running.
