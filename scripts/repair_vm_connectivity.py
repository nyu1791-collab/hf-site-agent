#!/usr/bin/env python3
"""Repair existing VM control services without changing credentials or jobs."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

HOME = Path.home()
STATE = HOME / '.local/state/hf-site-agent/connectivity'
PAUSE = HOME / '.config/hf-site-agent/connectivity.pause'

def run(argv, check=True):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=45)
    if check and result.returncode:
        raise RuntimeError('COMMAND_FAILED:' + argv[0])
    return result

def ctl(scope, *args):
    return ['systemctl'] + (['--user'] if scope == 'user' else []) + list(args)

def classify(unit, start):
    if not re.fullmatch(r'[A-Za-z0-9_.@:-]+\.service', unit):
        return None
    if re.search(r'desktop[-_ ]?commander|dc-remote', start, re.I):
        return 'desktop'
    if unit.startswith('actions.runner.') and 'hf-vm-control' in unit:
        return 'runner'
    return None

def discover():
    found = []
    for scope in ('user', 'system'):
        result = run(ctl(scope, 'list-unit-files', '--type=service', '--no-legend', '--no-pager'), False)
        if result.returncode:
            continue
        for line in result.stdout.splitlines():
            unit = line.split()[0]
            if not re.fullmatch(r'[A-Za-z0-9_.@:-]+\.service', unit):
                continue
            props = run(ctl(scope, 'show', unit, '-p', 'ExecStart', '-p', 'Type'), False).stdout
            # ExecStart may contain credentials; never print or persist it.
            role = classify(unit, props)
            if role and 'Type=oneshot' not in props:
                found.append({'scope': scope, 'unit': unit, 'role': role})
    return found

def write_file(path, content, system=False):
    if system:
        run(['sudo', '-n', 'mkdir', '-p', str(path.parent)])
        temp = STATE / 'dropin.tmp'
        temp.write_text(content)
        os.chmod(temp, 0o600)
        try:
            run(['sudo', '-n', 'install', '-m', '0644', str(temp), str(path)])
        finally:
            temp.unlink(missing_ok=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

def monitor(services):
    if PAUSE.exists():
        return {'status': 'PAUSED'}
    failures = []
    for service in services:
        scope, unit = service['scope'], service['unit']
        if run(ctl(scope, 'is-active', '--quiet', unit), False).returncode:
            prefix = ['sudo', '-n'] if scope == 'system' else []
            run(prefix + ctl(scope, 'reset-failed', unit), False)
            if run(prefix + ctl(scope, 'start', unit), False).returncode:
                failures.append(unit)
    return {'status': 'SERVICES_ACTIVE' if not failures else 'RECOVERY_BLOCKED', 'failed_units': failures}

def install(services, restart_desktop):
    if sum(s['role'] == 'desktop' for s in services) != 1:
        raise RuntimeError('EXACTLY_ONE_EXISTING_DESKTOP_SERVICE_REQUIRED')
    if sum(s['role'] == 'runner' for s in services) != 1:
        raise RuntimeError('EXACTLY_ONE_EXISTING_PRIVATE_RUNNER_REQUIRED')
    if PAUSE.exists():
        raise RuntimeError('MAINTENANCE_PAUSE_ACTIVE')
    if any(s['scope'] == 'system' for s in services):
        run(['sudo', '-n', 'true'])
    # Confirm user bus before any mutation.
    run(ctl('user', 'show-environment'))
    user = run(['id', '-un']).stdout.strip()
    linger = run(['loginctl', 'show-user', user, '-p', 'Linger', '--value']).stdout.strip()
    if linger != 'yes':
        run(['sudo', '-n', 'loginctl', 'enable-linger', user])
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    for service in services:
        base = HOME / '.config/systemd/user' if service['scope'] == 'user' else Path('/etc/systemd/system')
        path = base / (service['unit'] + '.d') / '70-hf-connectivity.conf'
        content = '[Unit]\nStartLimitIntervalSec=0\n\n[Service]\nRestart=always\nRestartSec=30\n'
        write_file(path, content, service['scope'] == 'system')
    (STATE / 'services.json').write_text(json.dumps(services))
    target = STATE / 'repair_vm_connectivity.py'
    if Path(__file__).resolve() != target.resolve():
        target.write_bytes(Path(__file__).read_bytes())
    os.chmod(target, 0o700)
    units = HOME / '.config/systemd/user'
    # Quotes protect paths if this user's home contains spaces.
    command = '"' + sys.executable + '" "' + str(target) + '" --monitor'
    write_file(units / 'hf-connectivity-watchdog.service', '[Unit]\nDescription=Recover existing VM control services\n\n[Service]\nType=oneshot\nExecStart=' + command + '\n')
    write_file(units / 'hf-connectivity-watchdog.timer', '[Unit]\nDescription=Check VM control services every minute\n\n[Timer]\nOnBootSec=60\nOnUnitActiveSec=60\nAccuracySec=1s\nRandomizedDelaySec=0\nUnit=hf-connectivity-watchdog.service\n\n[Install]\nWantedBy=timers.target\n')
    for scope in {s['scope'] for s in services}:
        run((['sudo', '-n'] if scope == 'system' else []) + ctl(scope, 'daemon-reload'))
    run(ctl('user', 'daemon-reload'))
    for service in services:
        prefix = ['sudo', '-n'] if service['scope'] == 'system' else []
        run(prefix + ctl(service['scope'], 'enable', service['unit']))
        if service['role'] == 'desktop' and restart_desktop:
            run(prefix + ctl(service['scope'], 'reset-failed', service['unit']), False)
            run(prefix + ctl(service['scope'], 'restart', service['unit']))
    run(ctl('user', 'enable', '--now', 'hf-connectivity-watchdog.timer'))
    return monitor(services)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--install', action='store_true')
    parser.add_argument('--restart-desktop', action='store_true')
    parser.add_argument('--monitor', action='store_true')
    args = parser.parse_args()
    try:
        metadata = run(['curl', '--noproxy', '*', '-fsS', '--max-time', '3', '-H', 'Metadata-Flavor: Google', 'http://metadata.google.internal/computeMetadata/v1/instance/name']).stdout.strip()
        if metadata != 'instance-20261001-071545':
            raise RuntimeError('TARGET_VM_IDENTITY_MISMATCH')
        services = json.loads((STATE / 'services.json').read_text()) if args.monitor else discover()
        result = monitor(services) if args.monitor else install(services, args.restart_desktop) if args.install else {'status': 'DISCOVERED', 'services': services}
        print(json.dumps(result))
        return 0 if result['status'] not in ('RECOVERY_BLOCKED',) else 2
    except Exception as exc:
        # Command output, environment and credentials never enter errors.
        print(json.dumps({'status': 'BLOCKED', 'reason': str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__}))
        return 2

if __name__ == '__main__':
    sys.exit(main())
