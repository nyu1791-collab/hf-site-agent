#!/usr/bin/env python3
"""Seal only verified reusable media preparation artifacts into a cache root.

The planner never treats a previous status label as proof.  This helper copies
the bounded preparation outputs into the cache namespace, records a content
hash for each bundle, and writes a checkpoint that a future plan can verify.
It deliberately does not certify partial render, preview or final-video data.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.media_speed_orchestrator import file_fingerprint


def _copy(source: Path, destination: Path) -> None:
    if not source.exists():
        raise RuntimeError(f"missing verified source: {source}")
    if destination.exists():
        if destination.is_dir():
            shutil.rmtree(destination)
        else:
            destination.unlink()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, destination)
    else:
        shutil.copy2(source, destination)


def _bundle(cache_root: Path, output_dir: Path, relative: str, sources: list[str]) -> dict[str, str]:
    destination = cache_root / relative
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for source_name in sources:
        source = output_dir / source_name
        _copy(source, destination / Path(source_name).name)
    return {"path": relative, "sha256": file_fingerprint(destination)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--cache-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--checkpoint-out", required=True, type=Path)
    args = parser.parse_args()

    plan: dict[str, Any] = json.loads(args.plan.read_text(encoding="utf-8"))
    if plan.get("status") != "READY" or plan.get("execution_blocked"):
        raise RuntimeError("blocked or non-ready plan cannot be sealed")
    stages = plan.get("stages")
    if not isinstance(stages, dict):
        raise RuntimeError("plan stages missing")

    root = args.cache_root
    root.mkdir(parents=True, exist_ok=True)
    bundles = {
        "voice_and_measured_timing": _bundle(root, args.output_dir, "verified/voice", ["voice", "timing.json", "voice-contract.json"]),
        "rights_verified_visual_assets": _bundle(root, args.output_dir, "verified/visuals", ["visual_assets.json"]),
        "character_shell_and_toolchain_prep": _bundle(root, args.output_dir, "verified/character", ["character-shell.json", "static_inventory.json", "static-portraits"]),
    }
    for stage, artifact in bundles.items():
        row = stages.get(stage)
        if not isinstance(row, dict):
            raise RuntimeError(f"plan stage missing: {stage}")
        row["status"] = "VERIFIED"
        row["artifact"] = artifact

    plan["checkpoint"] = {
        "schema_version": "media-speed-checkpoint-v1",
        "verified_preparation_only": True,
        "artifact_root_relative": True,
        "partial_render_or_final_never_certified": True,
    }
    args.checkpoint_out.parent.mkdir(parents=True, exist_ok=True)
    args.checkpoint_out.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "SEALED", "checkpoint": str(args.checkpoint_out), "stages": sorted(bundles)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
