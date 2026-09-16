# Media Batch Command Center

Status: bounded deterministic execution layer under the permanent Batch Media Orchestration standard.

## Purpose

Turn the existing three-job scheduler into a practical short-form clipping path. A locked manifest can describe up to 10 independent jobs; at most 3 run concurrently when resources permit. Later jobs queue. Mechanical media work stays deterministic and does not require an AI provider.

## Safety and quality contract

- Rights are checked per job before media execution.
- Source SHA-256 is frozen by `lock` and revalidated before `plan` or `run`.
- Mutating jobs use `run_batch_with_leases`; one job has one writer.
- Parallelism is capped at 3 and reduces to 1 in `DEGRADED` state or 0 in `PAUSED` state.
- Each rendered clip is machine-gated with ffprobe before promotion.
- Vertical modes produce 1080x1920, H.264, yuv420p, 30 fps, AAC, 48 kHz stereo.
- Inputs without audio receive deterministic silence rather than failing the batch.
- A failed job does not cancel unrelated jobs.
- Previous good output is kept until a replacement passes the machine gate. If sidecar manifest commit fails, the previous output is restored.
- Re-running an identical committed job reuses the validated checkpoint instead of re-rendering.
- Paid calls = 0. External video SaaS calls = 0.

## Manifest workflow

Start from `examples/media_batch_3way.template.json` and point each job at a rights-cleared local source file.

```bash
python scripts/media_batch_command_center.py lock \
  --manifest examples/media_batch_3way.template.json \
  --output work/media-batch.lock.json

python scripts/media_batch_command_center.py plan \
  --manifest work/media-batch.lock.json \
  --max-parallel 3

python scripts/media_batch_command_center.py run \
  --manifest work/media-batch.lock.json \
  --max-parallel 3 \
  --state NORMAL
```

`lock` computes source hashes. `plan` performs validation without FFmpeg rendering. `run` performs deterministic clipping and writes `batch-report.json` plus one sidecar manifest per output.

## Layout modes

- `vertical-fit`: preserves the whole image and pads to 9:16.
- `vertical-crop`: fills 9:16 and center-crops overflow.
- `preserve`: keeps source dimensions but normalizes SAR and 30 fps.

## Failure behavior

Expected failure classes inherit the permanent batch policy: `RIGHTS_BLOCK`, `RESOURCE_EXHAUSTED`, `STALE_WRITE`, `DETERMINISTIC_MEDIA`, `TRANSIENT_PROVIDER`, `CANCELLED`, and `INVALID_JOB`. This command center itself makes no provider call, so provider failures are only relevant if a future approved handler is layered above it.

## Verification

`tests/test_media_batch_command_center.py` covers locking, rights isolation, technical media contract validation, and rollback of previous healthy output. `tests/test_media_batch_command_center_ffmpeg.py` generates three synthetic local source videos and sends all three through the real FFmpeg/ffprobe path with effective parallel capacity set to 3. No paid or network media service is involved.
