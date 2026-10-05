#!/usr/bin/env python3
"""Install the existing local VOICEVOX engine as a private user service."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.request

UNIT = "voicevox.service"
ENGINE = Path("/home/n_yu1791/.local/share/voicevox_engine/linux-cpu-x64")
UNIT_CONTENT = """[Unit]
Description=Resident private VOICEVOX engine
After=network.target
StartLimitIntervalSec=0

[Service]
Type=simple
WorkingDirectory=/home/n_yu1791/.local/share/voicevox_engine/linux-cpu-x64
Environment=VV_CPU_NUM_THREADS=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
ExecStart=/home/n_yu1791/.local/share/voicevox_engine/linux-cpu-x64/run --host 127.0.0.1 --port 50021 --voicevox_dir /home/n_yu1791/.local/share/voicevox_engine/linux-cpu-x64 --cpu_num_threads 1 --output_log_utf8
Restart=always
RestartSec=3
Nice=10

[Install]
WantedBy=default.target
"""

def command(argv, *, check=True):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=15)
    if check and result.returncode:
        raise RuntimeError("COMMAND_FAILED:" + argv[0])
    return result

def control(*args, check=True):
    return command(["systemctl", "--user", *args], check=check)

def healthy():
    try:
        with urllib.request.urlopen("http://127.0.0.1:50021/version", timeout=2) as response:
            return bool(json.loads(response.read().decode("utf-8")))
    except Exception:
        return False

def install():
    if command(["id", "-un"]).stdout.strip() != "n_yu1791" or Path.home() != Path("/home/n_yu1791"):
        raise RuntimeError("TARGET_USER_MISMATCH")
    instance = command(["curl", "--noproxy", "*", "-fsS", "--max-time", "3", "-H", "Metadata-Flavor: Google", "http://metadata.google.internal/computeMetadata/v1/instance/name"]).stdout.strip()
    if instance != "instance-20261001-071545":
        raise RuntimeError("TARGET_VM_MISMATCH")
    swap = command(["/usr/sbin/swapon", "--show=NAME,SIZE", "--bytes", "--noheadings", "--raw"]).stdout
    # The first page is the swap header and is excluded from reported usable size.
    minimum_usable_swap = 2147483648 - os.sysconf("SC_PAGE_SIZE")
    if not any(row.split()[0] == "/hf-connectivity.swap" and int(row.split()[1]) >= minimum_usable_swap for row in swap.splitlines() if len(row.split()) >= 2):
        raise RuntimeError("MEMORY_GUARD_REQUIRED_FIRST")
    if not os.access(ENGINE / "run", os.X_OK):
        raise RuntimeError("EXISTING_ENGINE_REQUIRED")
    runtime = "/run/user/" + command(["id", "-u"]).stdout.strip()
    os.environ["XDG_RUNTIME_DIR"] = runtime
    os.environ["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=" + runtime + "/bus"
    control("show-environment")
    if command(["loginctl", "show-user", "n_yu1791", "-p", "Linger", "--value"]).stdout.strip() != "yes":
        raise RuntimeError("EXISTING_LINGER_REQUIRED")
    path = Path.home() / ".config/systemd/user" / UNIT
    if path.is_symlink() or (path.exists() and path.read_text(encoding="utf-8") != UNIT_CONTENT):
        raise RuntimeError("EXISTING_VOICEVOX_UNIT_DIFFERS")
    loaded = control("show", UNIT, "-p", "LoadState", "--value", check=False).stdout.strip()
    if loaded not in ("", "not-found") and not path.is_file():
        raise RuntimeError("EXISTING_VOICEVOX_UNIT_IS_NOT_OWNED")
    active = control("is-active", "--quiet", UNIT, check=False).returncode == 0
    ready = healthy()
    if ready and not active:
        raise RuntimeError("HEALTHY_UNMANAGED_ENGINE_LEFT_UNTOUCHED")
    listeners = command(["ss", "-H", "-ltn", "sport = :50021"]).stdout.strip()
    if listeners and not active:
        raise RuntimeError("UNMANAGED_PORT_50021_LEFT_UNTOUCHED")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        descriptor, temporary = tempfile.mkstemp(prefix=".voicevox-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(UNIT_CONTENT)
            os.chmod(temporary, 0o644)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
    control("daemon-reload")
    control("enable", UNIT)
    if not ready:
        control("reset-failed", UNIT, check=False)
        control("restart" if active else "start", UNIT)
    deadline = time.monotonic() + 60
    while not healthy():
        if time.monotonic() >= deadline:
            raise RuntimeError("VOICEVOX_SERVICE_HEALTH_TIMEOUT")
        time.sleep(1)
    properties = control("show", UNIT, "-p", "ActiveState", "-p", "UnitFileState", "-p", "Restart", "-p", "RestartUSec").stdout
    return {"status": "VOICEVOX_RESIDENT_READY", "endpoint": "http://127.0.0.1:50021", "cpu_threads": 1, "properties": properties.strip()}

def main():
    try:
        print(json.dumps(install()))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__}))
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
