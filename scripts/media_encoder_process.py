"""Bounded cleanup for a streaming encoder; frames and settings are unchanged."""
from contextlib import contextmanager
import subprocess


def interrupt_encoder(signum, frame):
    # Let the context manager clean up the child on CLI SIGTERM as well as Ctrl-C.
    raise SystemExit(128 + signum)


@contextmanager
def encoder_process(command, *, flush_timeout=60, stop_timeout=3):
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    try:
        yield process
        process.stdin.close()
        if process.wait(timeout=flush_timeout):
            raise RuntimeError("landscape encode failed")
    except BaseException:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=stop_timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=stop_timeout)
        else:
            process.wait()
        raise
    finally:
        try:
            if process.stdin is not None:
                process.stdin.close()
        except (OSError, BrokenPipeError):
            pass
