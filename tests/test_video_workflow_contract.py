"""Guard the actual launch/recovery contract before an expensive cloud build."""
from pathlib import Path
import re
import subprocess
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class VideoWorkflowContractTests(unittest.TestCase):
    def setUp(self):
        self.workflow = yaml.safe_load((ROOT / ".github/workflows/build-gemini4-argon-cloud-video.yml").read_text())
        self.steps = self.workflow["jobs"]["build"]["steps"]

    def test_launch_passes_required_id_and_never_cancels_or_uses_old_output(self):
        self.assertFalse(self.workflow["concurrency"]["cancel-in-progress"])
        commands = "\n".join(step.get("run", "") for step in self.steps)
        self.assertIn('--content-run-id "$CONTENT_RUN_ID"', commands)
        self.assertNotIn("/usr/bin/time", commands)
        self.assertNotIn("10min_landscape", commands)
        self.assertNotIn("ffprobe -", commands, "builder owns the one delivery probe")
        for step in self.steps:
            if step.get("name") in {
                "Install only missing media tools", "Resolve Zundamon and Metan shell cache-first",
                "Start one local VOICEVOX engine", "Build Gemini 4 Argon longform through canonical one-pass renderer",
            }:
                self.assertEqual(step["if"], "steps.finished.outputs.ready != 'true'")

    def test_job_cache_is_scoped_to_id_and_mission_and_saved_on_failure(self):
        restore = next(step for step in self.steps if step.get("id") == "job-cache")
        self.assertIn("inputs.content_run_id", restore["with"]["key"])
        self.assertIn("hashFiles(inputs.mission)", restore["with"]["key"])
        save = next(step for step in self.steps if step.get("uses") == "actions/cache/save@v4")
        self.assertIn("always()", save["if"])
        self.assertEqual(restore["with"]["key"], save["with"]["key"])

    def test_embedded_shell_and_python_are_syntactically_valid(self):
        for step in self.steps:
            script = step.get("run")
            if not script:
                continue
            result = subprocess.run(["bash", "-n"], input=script, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, step["name"] + result.stderr)
            for source in re.findall(r"<<'PY'\n(.*?)\nPY(?:\n|$)", script, re.S):
                compile(source, step["name"], "exec")

    def test_legacy_fixed_job_never_starts_from_a_code_push(self):
        legacy = yaml.safe_load((ROOT / ".github/workflows/durable-gemini4-media.yml").read_text())
        events = legacy.get("on", legacy.get(True))
        self.assertNotIn("push", events)
        self.assertIn("workflow_dispatch", events)


if __name__ == "__main__":
    unittest.main()
