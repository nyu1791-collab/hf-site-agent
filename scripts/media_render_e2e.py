"""Private fixed-fixture acceptance through the existing external render protocol.

Owns only an additive test-job table. Never initializes/replaces the live queue,
calls an LLM, approves external assets, or publishes the resulting video.
"""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import struct
import time
import zlib

from scripts.media_news_pipeline import synthesize_voice
from scripts.media_render_transport import dispatch_remote_render, verify_saved_remote_render, RenderTransportError

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/temporary_render_e2e.json"


def _png(path, width, height, rectangles):
    """Materialize fixed original raster artwork, without any generative API."""
    pixels = bytearray(width * height * 4)
    for x0, y0, x1, y1, color in rectangles:
        for y in range(y0, min(y1, height)):
            for x in range(x0, min(x1, width)):
                index = (y * width + x) * 4
                pixels[index:index+4] = bytes(color)
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    rows = b"".join(b"\0" + pixels[y*width*4:(y+1)*width*4] for y in range(height))
    encoded = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != encoded:
        raise RuntimeError("existing fixture asset differs; refusing to replace it")
    if not path.exists():
        path.write_bytes(encoded)


def materialize_shell(destination: Path) -> Path:
    """Abstract green/pink test figures with authored mouth variants, not public content assets."""
    # Keep the fixture's layer names aligned with the existing renderer.
    layer_names = {
        "Zundamon": ["尻尾的なアレ.png", "服装1/いつもの服.png", "服装1/左腕/基本.png", "服装1/右腕/基本.png", "枝豆/枝豆通常.png", "口/むー.png", "口/ほー.png", "口/お.png", "目/目セット/普通白目.png", "目/目セット/黒目/普通目.png", "眉/普通眉.png", "目/にっこり.png", "眉/上がり眉.png", "眉/困り眉1.png"],
        "Metan": ["ツインドリル右.png", "ツインドリル左.png", "白ロリ服/体.png", "白ロリ服/左腕/普通.png", "白ロリ服/右腕/普通.png", "前髪もみあげ.png", "頭部アクセサリ/髪留めフリル.png", "口/んー.png", "口/お.png", "口/わあー.png", "目/目セット/普通白目.png", "目/目セット/黒目/普通目.png", "眉/ごきげん.png", "目/目閉じ.png", "眉/こまり.png"]}
    for character, names in layer_names.items():
        color = (90, 175, 115, 255) if character == "Zundamon" else (195, 105, 160, 255)
        for index, name in enumerate(names):
            rectangles = []
            if index == 0:
                rectangles = [(65, 210, 192, 490, color), (35, 35, 222, 220, (248, 231, 215, 255)), (60, 100, 80, 120, (25, 40, 45, 255)), (176, 100, 196, 120, (25, 40, 45, 255))]
            if name.startswith("口/"):
                state = [p for p in names if p.startswith("口/")].index(name)
                rectangles = [(98, 165, 157, 170 + state*14, (90, 35, 50, 255))]
            _png(destination / character / name, 256, 512, rectangles)
    return destination


def prepare_package(workspace: Path, job_id: str) -> tuple[Path, dict]:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    source_id = hashlib.sha256(("temporary-render-e2e:" + job_id).encode()).hexdigest()
    package = workspace / "temporary-render-e2e" / source_id
    package.mkdir(parents=True, exist_ok=True, mode=0o700)
    mission = {"mission_id": "temporary-render-" + job_id, "source_id": source_id,
               "source_sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
               "title": fixture["title"], "scenes": [{"scene_id": "connection", "dialogue": fixture["dialogue"]}]}
    mission_path = package / "mission.json"
    if mission_path.exists() and json.loads(mission_path.read_text()) != mission:
        raise RuntimeError("saved test script differs; refusing to replace it")
    if not mission_path.exists():
        mission_path.write_text(json.dumps(mission, ensure_ascii=False), encoding="utf-8")
    synthesize_voice(package, min_seconds=1, max_seconds=90)
    timing = json.loads((package / "timing.json").read_text(encoding="utf-8"))
    visuals, assets = [], []
    url = "https://github.com/nyu1791-collab/hf-site-agent/blob/ai-army/provider-v3/tests/fixtures/temporary_render_e2e.json"
    display_url = "https://github.com/nyu1791-collab/hf-site-agent"
    for index in range(2):
        image = package / "images" / f"fixture-{index}.png"
        boxes = [(0, 0, 800, 400, (238, 243, 249, 255)), (45, 120, 275, 285, (90, 175, 115, 255)), (525, 120, 755, 285, (105, 140, 215, 255)), (275, 180 + index*35, 525, 190 + index*35, (50, 65, 90, 255)), ((490 if not index else 285), 160 + index*35, (525 if not index else 320), 210 + index*35, (50, 65, 90, 255))]
        _png(image, 800, 400, boxes)
        asset_id = f"fixture-{index}"
        visuals.append({"id": asset_id, "file": str(image.resolve()), "title": fixture["title"],
                        "source_url": display_url, "source_credit": "Original test diagram",
                        "media_region_only": True, "kind": "ORIGINAL_EXPLANATORY_DIAGRAM",
                        "generated": False, "asset_locator": str(image.resolve()), "license": "Repository-authored acceptance fixture"})
        assets.append({"id": asset_id, "file": str(image.resolve()), "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                       "downloaded": True, "selected_for_render": True, "rights_verified": True,
                       "rights_basis": fixture["rights_basis"], "rights_evidence_url": url, "credit": "Original test diagram"})
    for index, record in enumerate(timing["records"]):
        record["visual_id"] = f"fixture-{index % 2}"
        record["voicevox_speaker_id"] = record["style_id"]
    timing_path = package / "render-timing.json"
    timing_path.write_text(json.dumps(timing, ensure_ascii=False), encoding="utf-8")
    presentation = {"title": fixture["title"], "source_url": display_url, "source_credit": "Original acceptance fixtures",
                    "voice_credit": "VOICEVOX:ずんだもん・四国めたん", "media_region_only": True,
                    "sections": fixture["sections"], "visuals": visuals}
    presentation_path = package / "presentation.json"
    presentation_path.write_text(json.dumps(presentation, ensure_ascii=False), encoding="utf-8")
    return package, {"source_id": source_id, "presentation_path": presentation_path,
                     "timing_path": timing_path, "assets": assets, "duration_seconds": float(timing["total_duration"])}


def run_e2e(db: Path, workspace: Path, job_id: str) -> dict:
    if not __import__("re").fullmatch(r"[A-Za-z0-9_-]{1,64}", job_id):
        raise ValueError("unsafe test job ID")
    if not db.is_file():
        raise RuntimeError("existing coordinator queue required; no initialization permitted")
    conn = sqlite3.connect(db, timeout=30)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS render_e2e_jobs (job_id TEXT PRIMARY KEY, status TEXT NOT NULL, artifact TEXT, metadata TEXT NOT NULL, updated_at REAL NOT NULL)")
        row = conn.execute("SELECT status,artifact,metadata FROM render_e2e_jobs WHERE job_id=?", (job_id,)).fetchone()
        if row and row[0] == "success":
            meta = json.loads(row[2])
            result = verify_saved_remote_render(package=Path(row[1]).parent, source_id=meta["source_id"], expected_duration=meta["duration_seconds"])
            return {**result, "job_id": job_id, "queue_status": "success", "reused": True, "paid_llm_requests": 0}
        conn.execute("INSERT INTO render_e2e_jobs VALUES (?, 'preparing', NULL, '{}', ?) ON CONFLICT(job_id) DO UPDATE SET status='preparing',updated_at=excluded.updated_at", (job_id, time.time()))
        conn.commit()
        package, inputs = prepare_package(workspace.resolve(), job_id)
        try:
            if (package / "final.mp4").exists() or (package / "remote-render-report.json").exists():
                result = verify_saved_remote_render(package=package, source_id=inputs["source_id"], expected_duration=inputs["duration_seconds"])
            else:
                result = dispatch_remote_render(package=package, **inputs)
        except RenderTransportError as exc:
            conn.execute("UPDATE render_e2e_jobs SET status='waiting',metadata=?,updated_at=? WHERE job_id=?", (json.dumps({"reason": str(exc), "source_id": inputs["source_id"], "duration_seconds": inputs["duration_seconds"], "paid_llm_requests": 0}), time.time(), job_id))
            conn.commit()
            return {"job_id": job_id, "queue_status": "waiting", "reason": str(exc), "paid_llm_requests": 0}
        report = json.loads((package / "remote-render-report.json").read_text())
        metadata = {"source_id": inputs["source_id"], "duration_seconds": inputs["duration_seconds"],
                    "request_id": report["request_id"], "video_sha256": report["video_sha256"], "paid_llm_requests": 0}
        conn.execute("UPDATE render_e2e_jobs SET status='success',artifact=?,metadata=?,updated_at=? WHERE job_id=?", (result["video"], json.dumps(metadata), time.time(), job_id))
        conn.commit()
        return {**result, "job_id": job_id, "queue_status": "success", "ffprobe": report["video"], "paid_llm_requests": 0}
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--job")
    parser.add_argument("--materialize-test-shell", type=Path)
    args = parser.parse_args()
    if args.materialize_test_shell:
        print(json.dumps({"fixture_shell": str(materialize_shell(args.materialize_test_shell))}))
        return 0
    if not all((args.db, args.workspace, args.job)):
        parser.error("--db, --workspace and --job required")
    lock_dir = args.workspace / "temporary-render-e2e"
    lock_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (lock_dir / "e2e.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = run_e2e(args.db, args.workspace, args.job)
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["queue_status"] == "success" else 2


if __name__ == "__main__":
    raise SystemExit(main())
