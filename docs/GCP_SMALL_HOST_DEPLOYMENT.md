# GCP 2 GiB media host

## Layout

Keep the existing VM, checkout, SQLite database, and RSS cron. The current host layout shown in the SSH session is `~/hf-site-agent/runtime/media-queue.sqlite3`; the pipeline workspace is `~/hf-site-agent/runtime`. Do not run `init` against a new database, copy the database to a network filesystem, or install a second RSS poller.

Use one 2 GiB VM as the coordinator and local worker. Calls to the configured language-model API remain remote, VOICEVOX starts only inside the voice-synthesis command and exits when that command finishes, and FFmpeg runs once as a manual one-shot after the selected images pass the rights review. The SQLite pipeline lock prevents concurrent writers. No GPU or always-on media worker is part of this layout.

The initial target is `e2-small`: 2 GiB RAM with a shared-core sustained allowance of 0.5 vCPU. That is enough to try the sequential workflow, but it is CPU-limited and the five-minute creation target is not certified until actual job time and peak memory are measured. The user units cap each media job at 1,700 MiB so a failed job is stopped before it can consume the whole VM; a failed preparation/render remains recoverable from its saved checkpoint and does not publish. Preparation also pauses when one item is waiting for human rights review, and pauses below 2 GiB of free disk. It never deletes output automatically.

## Change the current VM when ready

In Google Cloud Console, open **Compute Engine → VM instances**, stop the existing `instance-20261001-071545` VM, then edit only its machine type to `e2-small` (2 GiB RAM, 0.5 sustained shared-core vCPU). Keep its existing boot disk, zone, service account, network settings and attached IP. Save and start the same VM. Do not create a second VM or resize/reinitialize the disk during this change. Confirm the selected project's billing-account status and remaining-credit expiry before starting it; the screenshot available for this setup still showed the Free Trial banner and an Upgrade button, so the paid-upgrade state is not yet verified.

If the measured render is too slow or reaches the 1,700 MiB job cap, the next measured option is `e2-medium` (4 GiB RAM, 1 sustained shared-core vCPU), which costs more. Do not select it based only on the five-minute target; first record one preparation and one render's elapsed time and peak memory.

## Existing host bootstrap

The unit files under `deploy/systemd/user/` are tailored to the existing `n_yu1791` home layout without changing the database. They are not installed or enabled by this repository change. After the repository update reaches the VM, install the files as that Linux user:

```bash
cd ~/hf-site-agent
mkdir -p ~/.config/systemd/user ~/.config/hf-site-agent
install -m 0644 deploy/systemd/user/hf-site-agent-media-news.service ~/.config/systemd/user/
install -m 0644 deploy/systemd/user/hf-site-agent-media-news.timer ~/.config/systemd/user/
install -m 0644 deploy/systemd/user/hf-site-agent-media-render@.service ~/.config/systemd/user/
systemctl --user daemon-reload
```

The existing RSS cron remains its own five-minute poller. The preparation timer is deliberately not enabled by installation: enabling it processes at most one inbox item per five-minute run and can make up to five exact-free script calls per UTC day. It does not use Google Cloud credits for OpenRouter, and it will not fall back to a paid route. Enable it only after a protected `media.env` contains the intended API key and the Engine has passed the local check:

```bash
sudo loginctl enable-linger "$USER"
systemctl --user enable --now hf-site-agent-media-news.timer
```

`media.env` is read from `~/.config/hf-site-agent/media.env`; keep it mode `0600`, never paste its secret into chat or command arguments. Set the expected VOICEVOX version after verifying `/version`. Install the official Linux CPU Engine at `~/.local/share/voicevox_engine/linux-cpu-x64`, or edit the unit's `VOICEVOX_ENGINE_DIR` to its verified location. The wrapper binds only to `127.0.0.1:50021`, uses one CPU thread, and stops only an Engine process it started itself.

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

Google's published `e2-small` compute price for `us-central1` is USD $0.016752855 per hour before disk, network, external IP, tax, and currency conversion. At 730 hours that is about USD $12.23 for VM compute alone. Check the selected VM's actual region and billing SKU before changing it. A Cloud Billing budget alert is a notification, not a general hard stop. The free-trial credit remains time-limited after upgrade; confirm the billing account's current status and credit expiry before leaving resources running.
