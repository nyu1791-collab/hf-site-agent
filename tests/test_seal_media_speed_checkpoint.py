import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.media_speed_orchestrator import plan_media_run


class SealMediaSpeedCheckpointTests(unittest.TestCase):
    def test_only_preparation_bundles_are_certified_with_hashes(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            cache = root / "cache"
            out = root / "out"
            out.mkdir()
            (out / "voice").mkdir()
            (out / "voice" / "line.wav").write_bytes(b"wav")
            (out / "timing.json").write_text("{}", encoding="utf-8")
            (out / "voice-contract.json").write_text("{}", encoding="utf-8")
            (out / "visual_assets.json").write_text("{}", encoding="utf-8")
            (out / "character-shell.json").write_text("{}", encoding="utf-8")
            (out / "static_inventory.json").write_text("{}", encoding="utf-8")
            (out / "static-portraits").mkdir()
            (out / "static-portraits" / "zundamon.png").write_bytes(b"z")
            plan = plan_media_run({"mission_or_script": "m", "cache_root": cache}, use_jev=False)
            plan_path = out / "speed-plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            checkpoint = cache / "mars" / "verified-plan.json"
            subprocess.run([
                "python", "scripts/seal_media_speed_checkpoint.py",
                "--plan", str(plan_path), "--cache-root", str(cache),
                "--output-dir", str(out), "--checkpoint-out", str(checkpoint),
            ], check=True)
            sealed = json.loads(checkpoint.read_text(encoding="utf-8"))
        for stage in ("voice_and_measured_timing", "rights_verified_visual_assets", "character_shell_and_toolchain_prep"):
            self.assertEqual(sealed["stages"][stage]["status"], "VERIFIED")
            self.assertTrue(sealed["stages"][stage]["artifact"]["sha256"])
        self.assertEqual(sealed["stages"]["one_pass_final_encode"]["status"], "PENDING")


if __name__ == "__main__":
    unittest.main()
