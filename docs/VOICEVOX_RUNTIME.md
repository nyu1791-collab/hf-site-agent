# VOICEVOX Runtime

The Engine is an HTTP service. Each host must verify its own Engine binary, version, speaker set and API health; a successful check from another machine or an earlier session is not evidence that this host is ready. This repository does not include the Engine binary or claim a remote tunnel is currently running.

## Local mode

By default, `scripts/with_local_voicevox.sh` starts the configured Engine from `VOICEVOX_ENGINE_DIR` on `127.0.0.1:50021`, reuses it if already healthy, checks `/version` and `/speakers`, and keeps it alive for the child command. An Engine started by the wrapper is stopped when that command exits or is interrupted; a healthy Engine that was already running is borrowed and left untouched. The default CPU thread count is one for small shared-core VMs. The wrapper does not bind the Engine to a public network interface.

```bash
bash scripts/with_local_voicevox.sh -- python scripts/video_creation_admission.py --runtime
```

For long-form synthesis, the synthesizer discovers speaker/style IDs from the live `/speakers` response; IDs from earlier runs must not be hard-coded.

## Optional remote worker for article-to-video staging

An already available worker computer can run VOICEVOX while a VPS coordinates the workflow. Start the Engine on the worker's loopback address, then initiate an SSH reverse tunnel from that computer to the VPS:

```bash
ssh -NT \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -R 127.0.0.1:50021:127.0.0.1:50021 \
  <dedicated-ssh-user>@<vps-host>
```

Set the preparation service environment on the VPS:

```text
VOICEVOX_REMOTE_TUNNEL=1
VOICEVOX_URL=http://127.0.0.1:50021
```

The remote mode accepts only `http://127.0.0.1:<port>`, requires an already healthy tunnel and validates the standard Zundamon/Metan speakers before synthesis. It will not start a local Engine when remote mode is selected. Restrict SSH forwarding to a dedicated account and keep the Engine port private. The tunnel, remote service and end-to-end audio output still need to be tested on the actual hosts.

In this layout the VOICEVOX inference runs on the worker. The coordinator still performs API calls, fetches images, transfers audio through the tunnel, runs FFmpeg conversion and renders the video. Offloading the full renderer needs a separate authenticated artifact-transfer worker and is not implemented here.

## Failure handling

The news pipeline reuses matching audio/timing checkpoints, retries a voice failure after 60 seconds and 5 minutes, and marks the source `VOICE_BLOCKED` after three failed attempts. Once the Engine/tunnel is repaired, the saved work can be requeued:

```bash
python -m scripts.media_news_pipeline --db <queue.db> --workspace <workspace> \
  retry-voice --source-id <source_id>
```

This requeues only the existing voice stage. It does not make a paid call or publish a video.

## Official references

- VOICEVOX Engine source and local HTTP API: https://github.com/VOICEVOX/voicevox_engine
- Follow the applicable voice library terms and required attribution for each character.
