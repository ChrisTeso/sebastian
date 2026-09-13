#!/usr/bin/env python3
"""Install and control only the new Sebastian per-user LaunchAgent."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sebastian.config import load_settings, private_read
from sebastian.daemon import AlreadyRunning, InstanceLock, TIMING_KEYS

LABEL = 'com.christeso.sebastian.native'
CONFIG = ROOT / '.private/config/installation.json'
APP = Path.home() / 'Applications/Sebastian Native.app'
PLIST = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')
DOMAIN = 'gui/' + str(os.getuid())
TARGET = DOMAIN + '/' + LABEL


def definition():
    python = ROOT / '.venv/bin/python'
    if not python.is_file():
        raise RuntimeError('Project virtual environment is missing')
    return {'Label': LABEL, 'ProgramArguments': ['/usr/bin/open', '-W', '-n', str(APP)],
            'WorkingDirectory': str(ROOT), 'RunAtLoad': True, 'KeepAlive': True,
            'ThrottleInterval': 10, 'ExitTimeOut': 45, 'Umask': 0o077,
            'EnvironmentVariables': {'HOME': str(Path.home()), 'PATH': '/usr/bin:/bin:/usr/sbin:/sbin'},
            'StandardOutPath': '/dev/null', 'StandardErrorPath': '/dev/null'}


def launchctl(*args):
    return subprocess.run(['/bin/launchctl', *args], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, check=False).returncode


def build_app():
    # Refuse in-place replacement while LaunchServices has this app running.
    if launchctl('print', TARGET) == 0:
        raise RuntimeError('Stop the service before installing')
    contents = APP / 'Contents'
    executable = contents / 'MacOS/Sebastian Native'
    for path in (APP, contents, executable.parent, executable, contents / 'Info.plist'):
        if path.is_symlink():
            raise PermissionError('App bundle must not contain symlinks')
    executable.parent.mkdir(parents=True, exist_ok=True)
    info = {'CFBundleIdentifier': LABEL, 'CFBundleName': 'Sebastian Native',
            'CFBundleDisplayName': 'Sebastian Native', 'CFBundleExecutable': 'Sebastian Native',
            'CFBundlePackageType': 'APPL', 'CFBundleVersion': '1', 'LSUIElement': True,
            'NSAppleEventsUsageDescription': 'Sebastian uses approved local applications to fulfill owner requests.',
            'SebastianRoot': str(ROOT)}
    with (contents / 'Info.plist').open('wb') as stream:
        plistlib.dump(info, stream)
    commands = [
        ['/usr/bin/xcrun', 'swiftc', str(ROOT / 'native/Launcher.swift'), '-framework', 'AppKit', '-o', str(executable)],
        ['/usr/bin/codesign', '--force', '--sign', '-', '--identifier', LABEL,
         '--requirements', '=designated => identifier "' + LABEL + '"', str(APP)],
        ['/usr/bin/codesign', '--verify', '--strict', str(APP)],
    ]
    for command in commands:
        if subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
            raise RuntimeError('Native launcher build/sign verification failed')


def install():
    load_settings(CONFIG)
    data = definition()
    build_app()
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    if PLIST.is_symlink():
        raise PermissionError('LaunchAgent plist must not be a symlink')
    import tempfile
    fd, name = tempfile.mkstemp(prefix='.sebastian-', dir=PLIST.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            plistlib.dump(data, stream)
        os.replace(name, PLIST)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    print('Installed LaunchAgent. Run start to load it; it starts automatically at future login.')


def status():
    settings = load_settings(CONFIG)
    result = {'label': LABEL, 'loaded': launchctl('print', TARGET) == 0, 'running': False}
    if settings.state_dir.exists():
        lock = None
        try:
            lock = InstanceLock(settings.state_dir / 'service.lock')
        except AlreadyRunning:
            result['running'] = True
        finally:
            if lock:
                lock.close()
        path = settings.state_dir / 'service-status.json'
        if path.exists():
            data = json.loads(private_read(path))
            # Fixed metadata projection: no arbitrary keys or diagnostic strings.
            for key in ('heartbeat_at', 'pid', 'runtime_pid'):
                if type(data.get(key)) in (int, float):
                    result[key] = data[key]
            for key in ('slack_connected', 'messages_readable'):
                result[key] = data.get(key) is True
            result['last_failure'] = data.get('last_failure') if data.get('last_failure') in ('engine_failure', 'service_failure') else None
            result['last_timings'] = {k: v for k, v in data.get('last_timings', {}).items() if k in TIMING_KEYS and type(v) in (int, float) and 0 <= v < 1e9}
            result['phase'] = data.get('phase') if data.get('phase') in ('starting', 'running', 'stopped') else 'unknown'
            import time
            result['heartbeat_fresh'] = result['running'] and 0 <= time.time() - data.get('heartbeat_at', 0) < 20
    print(json.dumps(result, indent=2))


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('install', 'start', 'stop', 'status'))
    command = parser.parse_args().command
    try:
        if command == 'install':
            install()
        elif command == 'status':
            status()
        elif command == 'start':
            if not PLIST.is_file():
                raise RuntimeError('Install the LaunchAgent first')
            if launchctl('enable', TARGET):
                raise RuntimeError('Could not enable LaunchAgent')
            if launchctl('print', TARGET) != 0 and launchctl('bootstrap', DOMAIN, str(PLIST)):
                raise RuntimeError('Could not load LaunchAgent')
            if launchctl('kickstart', TARGET):
                raise RuntimeError('Could not start LaunchAgent')
            print('Start requested. Use status to verify health.')
        else:
            # Disable first so stop persists over login and a restart cannot race bootout.
            if launchctl('disable', TARGET):
                raise RuntimeError('Could not disable LaunchAgent')
            if launchctl('print', TARGET) == 0 and launchctl('bootout', TARGET):
                raise RuntimeError('Could not stop LaunchAgent')
            executable = APP / 'Contents/MacOS/Sebastian Native'
            if executable.is_file() and subprocess.run([str(executable), '--stop'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=50).returncode:
                raise RuntimeError('Native launcher has not stopped cleanly')
            print('LaunchAgent and native app stopped and disabled. Use start to enable it again.')
        return 0
    except Exception:
        print('Service control failed; check installation, private config, and local permissions.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
