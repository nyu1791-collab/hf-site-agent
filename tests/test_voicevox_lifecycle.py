"""Verify that an Engine started for one synthesis is stopped on exit."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class VoicevoxLifecycleTests(unittest.TestCase):
    def run_wrapper(self, *, borrowed=False, failure=False):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)

            def executable(name, content):
                path = temp / name
                path.write_text(content)
                path.chmod(0o700)

            executable("curl", '#!/bin/bash\n[[ "$BORROWED" == 1 || -f "$PID_FILE" ]]\n')
            executable("python3", "#!/bin/bash\ncat >/dev/null\nexit 0\n")
            executable("run", '#!/bin/bash\nprintf "%s" "$$" > "$PID_FILE"\nprintf "%s" "$VV_CPU_NUM_THREADS" > "$THREADS_FILE"\nwhile true; do sleep 0.1; done\n')
            env = dict(os.environ, PATH=str(temp) + ":" + os.environ["PATH"],
                       VOICEVOX_ENGINE_DIR=str(temp), VOICEVOX_REMOTE_TUNNEL="0",
                       VOICEVOX_URL="http://127.0.0.1:50021",
                       PID_FILE=str(temp / "engine.pid"), THREADS_FILE=str(temp / "threads"),
                       BORROWED=str(int(borrowed)))
            result = subprocess.run(
                ["bash", str(ROOT / "scripts/with_local_voicevox.sh"), "--", "/bin/bash", "-c",
                 "exit 7" if failure else "exit 0"],
                env=env, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 7 if failure else 0, result.stderr)
            pidfile = temp / "engine.pid"
            if borrowed:
                self.assertFalse(pidfile.exists(), "must not launch another engine")
            else:
                self.assertTrue(pidfile.exists())
                self.assertEqual((temp / "threads").read_text(), "1")
                with self.assertRaises(ProcessLookupError):
                    os.kill(int(pidfile.read_text()), 0)

    def test_owned_engine_stops_after_success(self):
        self.run_wrapper()

    def test_owned_engine_stops_after_consumer_failure(self):
        self.run_wrapper(failure=True)

    def test_available_engine_is_reused(self):
        self.run_wrapper(borrowed=True)


if __name__ == "__main__":
    unittest.main()
