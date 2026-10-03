# Temporary external renderer acceptance

The user-created GCP VM `hf-render-worker` is a 72-hour acceptance worker.
The provider-neutral renderer and loopback SSH protocol remain the interface for
replacement by an Oracle host or home computer. No GCP SDK runs inside the renderer.

Use the current PR #40 head on both hosts. The private VM-control draft branch can
update and test the coordinator without changing `main`. The user-operated Cloud
Shell installer discovers the two existing VMs; it never creates, resizes or
deletes an instance, opens a firewall, changes paid routes or replaces secrets.

Initial render-only credentials do not currently exist. Setup therefore requires
the user to explicitly invoke `--provision-render-auth`. It provisions a random
render token in protected files and a restricted tunnel key without printing either.
The coordinator SSH host key is obtained from an authenticated GCP SSH session.
An existing auth/config file causes setup to stop instead of replacing it.

```bash
python3 setup_temporary_gcp_render_worker.py --project PROJECT_ID --operation setup --provision-render-auth --complete-e2e
```

`--complete-e2e` runs the fixed repository-authored fixture with existing local
VOICEVOX, renders the first MP4, checks both streams and duration, saves queue
success on the coordinator, waits for the worker to be idle, reboots it and waits
for authenticated health with a changed boot ID. It then renders the second job.
No human SSH action is required after the reboot. The receipt is saved at
`runtime/temporary-render-e2e/reboot-e2e-receipt.json` on the coordinator.

The fixture is private infrastructure acceptance material. Its abstract authored
character layers and explanatory diagrams are owned by the repository; they are
not a replacement for cleared editorial character assets in published videos.
The additive `render_e2e_jobs` table records only fixture jobs. RSS/news queue
states are preserved and are not falsely counted as completed by fixture runs.

The coordinator retains script, cached narration, render request/checkpoint,
verified video and report. The worker retains a request ledger, completed response
archive and render logs. An interrupted transfer is recovered by authenticated
GET for the same request ID; no accepted render or paid script is repeated. A reserved
request with no completed result stays waiting for diagnosis, rather than being
automatically replayed. Kernel file locks enforce one render and one package writer.
If the authenticated worker proves the ID was never accepted, an explicit resume
may send the saved archive with that same ID; a racing reservation still rejects
duplicates. A draining worker can serve completed results while rejecting new work.

The service has systemd watchdog/heartbeat, restart backoff, private state, bounded
logs and graceful SIGTERM. A process timeout kills the renderer/FFmpeg group.
After 72 hours admission becomes DRAINING, allowing already running work to finish.
The VM still incurs its normal costs until explicitly stopped.

Before stopping, run the installer with `--operation drain`. It rejects new jobs,
waits for the current render, saves the worker ledger/results/logs on the coordinator,
verifies the backup SHA-256 and stops renderer/tunnel. `--operation stop` also stops
the existing VM. It never deletes it. Queue, audio, scripts and verified videos stay
on the coordinator. `--operation resume` reopens a 72-hour admission window and
starts the worker/tunnel; starting a stopped VM remains an explicit GCP operation.

The local FFmpeg smoke uses mock PCM only and is not evidence of GCP VM reboot or
live VOICEVOX E2E. Only the saved live acceptance receipt and verified MP4s establish
completion of the user-requested temporary-worker phase.
