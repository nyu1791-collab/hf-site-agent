# GCP 2 GiB media host

## Layout

Keep the existing VM, checkout, SQLite database, and RSS cron. The current host layout shown in the SSH session is `~/hf-site-agent/runtime/media-queue.sqlite3`; the pipeline workspace is `~/hf-site-agent/runtime`. Do not run `init` against a new database, copy the database to a network filesystem, or install a second RSS poller.

Use one 2 GiB VM as the coordinator and local worker. Calls to the configured language-model API remain remote, VOICEVOX starts only inside the voice-synthesis command and exits when that command finishes, and FFmpeg runs once as a manual one-shot after the selected images pass the rights review. The SQLite pipeline lock prevents concurrent writers. No GPU or always-on media worker is part of this layout.

The initial target is `e2-small`: 2 GiB RAM with a shared-core sustained allowance of 0.5 vCPU. That is enough to try the sequential workflow, but it is CPU-limited and the five-minute creation target is not certified until actual job time and peak memory are measured. The user units cap each media job at 1,700 MiB so a failed job is stopped before it can consume the whole VM; a failed preparation/render remains recoverable from its saved checkpoint and does not publish. Preparation also pauses when one item is waiting for human rights review, and pauses below 2 GiB of free disk. It never deletes output automatically.

## Verify the existing VM before activation

The user identified instance-20261001-071545 as the existing Compute Engine VM; the supplied console screenshot showed it running as e2-small (2 GiB RAM). That screenshot is not live proof of current state. Re-open Compute Engine → VM instances and check the current project, VM name, zone, machine type, boot disk, service account, network, and power state. This deployment targets only that existing VM. Do not create a duplicate, stop or resize it, modify its disk, or reserve a static IP.

The user authorized approximately JPY 47,000 of Google Cloud credit as a hard ceiling. Before enabling the persistent timer, verify the live billing account, eligible remaining credit, expiry, project, region/SKU, and forecast in Google Cloud Console. Record a fresh, secret-free snapshot in ~/.config/hf-site-agent/cloud-budget.json with mode 0600; the activation preflight blocks if that evidence is missing, stale, belongs to another project, shows auto top-up, or forecasts more than the remaining credit. Google Cloud credit does not cover OpenRouter or any other third-party API bill. Vertex AI is not required or preferred.

Keep the current e2-small size unless live measurements justify an upgrade. The existing VM is already running; this process does not stop or resize it.

## Existing host bootstrap

The unit files under deploy/systemd/user/ target the existing n_yu1791 home layout and preserve the existing database. After the canonical branch reaches that VM, run the read-only preflight and then the authorized activation command as that Linux user:

```bash
cd ~/hf-site-agent
python3 -m scripts.install_gcp_small_host_services
python3 -m scripts.install_gcp_small_host_services --activate
```

The existing RSS cron remains the only source poller; this activation does not install a second poller. After every preflight passes, the preparation timer processes at most one inbox item per five-minute run and makes at most five exact-free script calls per UTC day. It does not use Google Cloud credits for OpenRouter and cannot fall back to a paid route. If a prerequisite fails, the timer stays disabled.

media.env is read from ~/.config/hf-site-agent/media.env; keep it mode 0600 and never paste its secret into chat or command arguments. Store a separate cloud-budget.json snapshot at ~/.config/hf-site-agent/cloud-budget.json with mode 0600; it contains billing facts only and no credentials. The activation script checks the protected media env, local VOICEVOX installation/version, free disk, existing queue integrity, canonical branch, GCP metadata identity, and recent billing evidence. It prints only blocker codes and service state. It never initializes the queue database, runs a paid API call, enables a render timer, or publishes.

Set the expected VOICEVOX version after verifying /version. Install the official Linux CPU Engine at ~/.local/share/voicevox_engine/linux-cpu-x64, or set VOICEVOX_ENGINE_DIR in the protected env file. The wrapper binds only to 127.0.0.1:50021, uses one CPU thread, and stops only an Engine process it started itself.

## One reviewed render

Set `MEDIA_RENDER_SHELL` and `MEDIA_RENDER_FONT` in `~/.config/hf-site-agent/media-render.env` to the already approved character shell and font. Keep that file mode `0600`. The renderer continues to require an `ASSET_REVIEW_REQUIRED` item with recorded reuse basis, HTTPS evidence URL, credit, and matching asset hashes. Then start exactly one local render for its 64-character lowercase source ID:

```bash
systemctl --user start 'hf-site-agent-media-render@<source_id>.service'
systemctl --user status 'hf-site-agent-media-render@<source_id>.service' --no-pager
```

This is a same-VM CPU render. It does not need the optional remote render worker, does not call an AI API, and does not publish. Only a verified render advances to `READY_TO_PUBLISH`.

## Check the result

Use the existing database path for all checks:

```bash
python3 -m scripts.media_source_ingress --db runtime/media-queue.sqlite3 status
free -h
df -h /
systemctl --user list-timers --all
```

After one permitted preparation and one reviewed render, record wall time and peak memory from the job's systemd cgroup/journal. If the job is OOM-killed or too slow, retain the queue and checkpoints; move to a 4 GiB `e2-medium` or a separately provisioned render host only after measuring. Do not add a GPU VM for the initial workload.

## Billing note

Google's published `e2-small` compute price for `us-central1` is USD $0.016752855 per hour before disk, network, external IP, tax, and currency conversion. At 730 hours that is about USD $12.23 for VM compute alone. Check the selected VM's actual region and billing SKU before changing it. A Cloud Billing budget alert is a notification, not a general hard stop. Confirm the authorized project credit's current status and expiry before leaving the existing VM or timer running.
