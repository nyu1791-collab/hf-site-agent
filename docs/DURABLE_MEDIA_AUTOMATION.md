# Durable Media Automation — Speed-First Foundation

This layer makes the existing media stack recoverable without turning the project into a five-agent chain. The repository implementation is a staging/runtime foundation; it does not mean an RSS poller or VPS daemon is already running.

## What is added

- `scripts/durable_media_runner.py`: SQLite/WAL persistent queue with idempotency, lease/heartbeat, retry, crash recovery, typed handler registry and `READY_TO_PUBLISH` stop state.
- `config/media_automation_fast_path.json`: maps source monitoring, script/tone, fact/compliance, render/metadata, supervision and analytics into the existing AI Army with the minimum number of agent hops.
- Existing `scripts/media_batch_command_center.py` remains the deterministic renderer. The durable runner does not duplicate FFmpeg logic.

## Current runtime boundary

- The runner currently executes a prepared `MEDIA_BATCH_RUN`; it is not yet an end-to-end news video producer.
- `scripts/media_source_ingress.py poll` performs a bounded one-shot poll of the fixed official OpenAI RSS feed and stores new items in SQLite as `PREPARATION_REQUIRED`.
- Polling is not scheduled as a daemon. Production VPS service, script/tone generation, VOICEVOX, and rights-cleared news visual acquisition are still unconnected.
- The production sequence still needs scheduled source selection, script and tone, VOICEVOX, rights-cleared news assets, and render integration.
- Public posting is outside this runner. Success means only `READY_TO_PUBLISH`.

## Fast path (target architecture)

`RSS/Web adapter -> stable source_id -> queue -> current AI Army judgment only when needed -> existing media command center -> minimum machine gate -> READY_TO_PUBLISH`

The normal path avoids a mandatory Fact Agent and Supervisor Agent. Fact/compliance becomes conditional. Supervisor becomes a machine gate unless a material failure or ambiguity requires an AI judgment. Analytics is asynchronous and never blocks video creation.

## Start

```bash
python scripts/durable_media_runner.py \
  --db runtime/media-queue.sqlite3 \
  --workspace . init
```

Create a typed job file:

```json
{
  "kind": "MEDIA_BATCH_RUN",
  "source_id": "rss:https://example.invalid/item/123",
  "priority": 10,
  "max_attempts": 2,
  "payload": {
    "manifest_path": "work/media-batch.lock.json",
    "max_parallel": 3,
    "state": "NORMAL"
  }
}
```

Then enqueue and run:

```bash
python scripts/durable_media_runner.py --db runtime/media-queue.sqlite3 --workspace . enqueue --job work/job.json
python scripts/durable_media_runner.py --db runtime/media-queue.sqlite3 --workspace . run --poll-seconds 0.2
```

## Queue and recovery details

Newly enqueued work receives a content-addressed, workspace-contained manifest snapshot. Relative paths are resolved using the same base as the renderer, then frozen to resolved paths. Input file hashes remain checked by the media command center. Existing queued jobs from earlier runner versions are pinned on their first claim, so a code update does not discard the queue. One runner lock is held per workspace; use one durable runner process for a workspace until a shared resource governor is added.

The runner's `health` command reports queue counts, expired leases and oldest pending age. It does not prove that an external VPS supervisor, VOICEVOX, RSS polling or platform credentials are healthy.

## One-shot RSS intake

Initialize the durable database, then poll the fixed feed. Repeating the poll does not duplicate inbox rows:

```bash
python scripts/durable_media_runner.py --db runtime/media-queue.sqlite3 --workspace . init
python -m scripts.media_source_ingress --db runtime/media-queue.sqlite3 poll --feed-id openai-news
python -m scripts.media_source_ingress --db runtime/media-queue.sqlite3 status
```

New records stay in `PREPARATION_REQUIRED`; polling never creates render jobs. After a rights-verified media manifest and its input files are ready, create a typed job spec whose `source_id` matches the inbox row, then promote it:

```bash
python -m scripts.media_source_ingress --db runtime/media-queue.sqlite3 prepare --source-id <stable-source-id> --job runtime/prepared-job.json --workspace work
```

The first source adapter is deliberately limited to one fixed official feed. Feed failures are reported per source; no arbitrary URL is accepted from a job. A scheduled daemon and automated script/voice/asset preparation remain future work.

## Implementation status and next stages

Implemented in the repository path: immutable manifest snapshots, renderer-consistent workspace path checks, input-hash verification by the renderer, lease-expiry fencing, one runner lock per workspace, bounded retries, legacy queued-job pinning, child environment isolation, and a stop at `READY_TO_PUBLISH`.

Not connected or not proven in a production environment: scheduled RSS/Web polling, novelty/importance selection, script/tone API execution, VOICEVOX daemon operation, end-to-end asset acquisition and video creation, VPS/systemd installation, reboot recovery on a persistent host, and SNS publishing. The first rollout target is a staging workspace with a persistent database; live production credentials and external publishing remain outside this queue.

Before enabling a long-running service, validate it with a synthetic local source through enqueue, render and `ffprobe`, then restart the runner at each boundary and confirm it resumes from the saved input/checkpoint. Add output-path keyed exclusion and renewable render leases before allowing concurrent render processes to share an output workspace.

## Security boundary kept even in maximum-speed mode

The queue never accepts a shell command, executable, argv or provider secret. Job and manifest paths are resolved under the configured workspace. Lease tokens are stored in the SQLite queue only as SHA-256 hashes. A separate short-lived media-render lease is filesystem-backed and is local to the output workspace. Child media processes inherit only a minimal environment and do not receive API keys. Public publishing is not performed by this runner; successful jobs stop at `READY_TO_PUBLISH` under the current repository authorization boundary.
