#!/usr/bin/env bash
set -euo pipefail

# Keep VOICEVOX and the consumer command in one execution session. Some hosted
# tool runners isolate the network namespace for each command invocation.
ENGINE_DIR="${VOICEVOX_ENGINE_DIR:-/tmp/devday-voicevox/extracted/linux-cpu-x64}"
BASE_URL="${VOICEVOX_URL:-http://127.0.0.1:50021}"
THREADS="${VV_CPU_NUM_THREADS:-4}"
REMOTE_TUNNEL="${VOICEVOX_REMOTE_TUNNEL:-0}"
ENGINE_PID=""
ENGINE_LOG=""

cleanup() {
  if [[ -n "$ENGINE_PID" ]]; then
    kill "$ENGINE_PID" 2>/dev/null || true
    wait "$ENGINE_PID" 2>/dev/null || true
  fi
  if [[ -n "$ENGINE_LOG" ]]; then
    rm -f "$ENGINE_LOG"
  fi
}
trap cleanup EXIT INT TERM

if [[ "${1:-}" == "--" ]]; then
  shift
fi

if [[ "$REMOTE_TUNNEL" == "1" ]]; then
  if [[ ! "$BASE_URL" =~ ^http://127\.0\.0\.1:([0-9]{1,5})$ ]]; then
    echo "Remote VOICEVOX must use a loopback-only HTTP tunnel URL." >&2
    exit 2
  fi
  TUNNEL_PORT="${BASH_REMATCH[1]}"
  if ((10#$TUNNEL_PORT < 1 || 10#$TUNNEL_PORT > 65535)); then
    echo "Remote VOICEVOX tunnel port is out of range." >&2
    exit 2
  fi
elif [[ "$BASE_URL" != "http://127.0.0.1:50021" ]]; then
  echo "Local VOICEVOX_URL must remain http://127.0.0.1:50021" >&2
  exit 2
fi

if [[ "$REMOTE_TUNNEL" != "1" && ! -x "$ENGINE_DIR/run" ]]; then
  echo "Local VOICEVOX Engine executable not found at $ENGINE_DIR/run" >&2
  echo "Set VOICEVOX_ENGINE_DIR, or configure an SSH loopback tunnel." >&2
  exit 2
fi

if ! curl --silent --show-error --fail --max-time 2 "$BASE_URL/version" >/dev/null 2>&1; then
  if [[ "$REMOTE_TUNNEL" == "1" ]]; then
    echo "Remote VOICEVOX tunnel is unavailable; leaving the job queued for retry." >&2
    exit 2
  fi
  ENGINE_LOG="${TMPDIR:-/tmp}/voicevox-engine-$$.log"
  VV_CPU_NUM_THREADS="$THREADS" "$ENGINE_DIR/run" \
    --host 127.0.0.1 \
    --port 50021 \
    --voicevox_dir "$ENGINE_DIR" \
    --cpu_num_threads "$THREADS" \
    --output_log_utf8 >"$ENGINE_LOG" 2>&1 &
  ENGINE_PID=$!
  ready=0
  for _ in $(seq 1 60); do
    if curl --silent --show-error --fail --max-time 2 "$BASE_URL/version" >/dev/null 2>&1; then
      ready=1
      break
    fi
    if ! kill -0 "$ENGINE_PID" 2>/dev/null; then
      cat "$ENGINE_LOG" >&2
      exit 1
    fi
    sleep 1
  done
  if [[ "$ready" != 1 ]]; then
    cat "$ENGINE_LOG" >&2
    echo "VOICEVOX Engine did not become ready within 60 seconds." >&2
    exit 1
  fi
fi

python3 - "$BASE_URL" <<'PY'
import json
import os
import sys
import urllib.request

base = sys.argv[1]
def get(path):
    with urllib.request.urlopen(base + path, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))

version = get("/version")
speakers = get("/speakers")
expected_version = os.environ.get("VOICEVOX_EXPECTED_VERSION")
if expected_version and str(version) != expected_version:
    raise SystemExit("VOICEVOX Engine version does not match the configured expected version.")
for expected in ("ずんだもん", "四国めたん"):
    speaker = next((item for item in speakers if item.get("name") == expected), None)
    styles = (speaker or {}).get("styles", [])
    if not any(style.get("name") == "ノーマル" and isinstance(style.get("id"), int) for style in styles):
        raise SystemExit(f"VOICEVOX standard cast unavailable: {expected}")
print(json.dumps({"status": "PASS", "version": version, "speakers": ["ずんだもん", "四国めたん"]}, ensure_ascii=False))
PY

if [[ "$#" -eq 0 ]]; then
  echo "VOICEVOX Engine is ready at $BASE_URL; press Ctrl-C to stop." >&2
  if [[ -n "$ENGINE_PID" ]]; then
    wait "$ENGINE_PID"
  else
    while true; do sleep 3600; done
  fi
else
  "$@"
fi
