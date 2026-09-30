# Durable Media Automation — Speed-First Foundation

This layer makes the existing media stack recoverable without turning the project into a five-agent chain.

## What is added

- `scripts/durable_media_runner.py`: SQLite/WAL persistent queue with idempotency, lease/heartbeat, retry, crash recovery, typed handler registry and `READY_TO_PUBLISH` stop state.
- `config/media_automation_fast_path.json`: maps source monitoring, script/tone, fact/compliance, render/metadata, supervision and analytics into the existing AI Army with the minimum number of agent hops.
- Existing `scripts/media_batch_command_center.py` remains the deterministic renderer. The durable runner does not duplicate FFmpeg logic.

## Fast path

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

## Security boundary kept even in maximum-speed mode

The queue never accepts a shell command, executable, argv or provider secret. Job and manifest paths are resolved under the configured workspace. Lease tokens are stored only as SHA-256 hashes. Child media processes inherit only a minimal environment and do not receive API keys. Public publishing is not performed by this runner; successful jobs stop at `READY_TO_PUBLISH` under the current repository authorization boundary.
