#!/usr/bin/env bash
set -euo pipefail

ROOT="${HF_SITE_AGENT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
VENV="${GEMINI_VIDEO_VENV:-$HOME/.venvs/hf-site-agent-gemini-video}"
PYTHON="${PYTHON_BIN:-python3}"
REQ_VERSION="${GOOGLE_GENAI_VERSION:-2.28.0}"

if [[ ! -x "$VENV/bin/python" ]]; then
  mkdir -p "$(dirname "$VENV")"
  "$PYTHON" -m venv "$VENV"
fi

if ! "$VENV/bin/python" -c 'import google.genai' >/dev/null 2>&1; then
  "$VENV/bin/python" -m pip install --disable-pip-version-check "google-genai==$REQ_VERSION"
fi

"$VENV/bin/python" -c 'from google import genai; print("gemini-video-runtime-ready")'
printf 'venv=%s\n' "$VENV"
printf 'runner=%s\n' "$ROOT/scripts/gemini_video_director.py"
