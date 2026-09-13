#!/usr/bin/env python3
"""Read-only installation privacy check; output contains booleans/counts only."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def safe_path(path, *, private=False, directory=False):
    path = Path(path)
    if not path.is_absolute() or not path.exists():
        return False
    if any(p.is_symlink() for p in (path, *path.parents)):
        return False
    info = path.stat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    return (expected(info.st_mode) and info.st_uid == os.getuid()
            and (directory or info.st_nlink == 1)
            and (not private or not info.st_mode & 0o077))


def protected_by_owner_directory(path):
    return any(p.is_dir() and p.stat().st_uid == os.getuid()
               and not p.stat().st_mode & 0o077 for p in Path(path).parents)


def count_matches(paths, values):
    """Never return matched content, file names, or secret values."""
    count = 0
    for path in paths:
        if path.is_file() and not path.is_symlink():
            content = path.read_bytes()
            count += sum(bool(value and value in content) for value in values)
    return count


def audit(root=ROOT):
    private = root / '.private'
    config_path = private / 'config/installation.json'
    checks = {'private_root_safe': safe_path(private, private=True, directory=True),
              'installation_file_safe': safe_path(config_path, private=True)}
    if not all(checks.values()):
        return checks
    config = json.loads(config_path.read_text())
    checks['same_group_authorization_enabled'] = config.get('owner_group_replies') is True
    for key in ('state_dir', 'restricted_workspace'):
        path = Path(config[key])
        checks[key + '_safe'] = (safe_path(path, private=True, directory=True)
            and path.is_relative_to(private) and path != private)
    checks['workspaces_separated'] = Path(config['restricted_workspace']).resolve() != Path(config['owner_workspace']).resolve()
    secrets = [Path(config['slack'][key]) for key in ('app_token_file', 'bot_token_file')]
    secrets.append(private / 'probes/executor-key')
    checks['secret_files_safe'] = all(safe_path(p, private=True) and p.is_relative_to(private) for p in secrets)
    values = [p.read_bytes().strip() for p in secrets] if checks['secret_files_safe'] else []
    listed = subprocess.run(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=root, capture_output=True, check=True).stdout
    ordinary = [root / os.fsdecode(p) for p in listed.split(b'\0') if p]
    checks['ordinary_secret_matches'] = count_matches(ordinary, values)
    ignored = subprocess.run(['git', 'check-ignore', '--no-index', '-q', str(private / 'audit-sentinel')], cwd=root).returncode == 0
    checks['private_directory_ignored'] = ignored
    checks['private_files_tracked'] = sum(p.is_relative_to(private) for p in ordinary)
    recovery = Path.home() / '.codex/recovery/sebastian-20260912T162029Z'
    checks['recovery_outside_workspace'] = not recovery.is_relative_to(root)
    checks['recovery_owner_only'] = safe_path(recovery, private=True, directory=True) and all(safe_path(p, private=True) for p in recovery.iterdir())
    native_metadata = private / 's5-runtime/sessions.json'
    ids = set(json.loads(native_metadata.read_text()).values()) if native_metadata.exists() else set()
    sessions = Path.home() / '.codex/sessions'
    # Only stat exact known synthetic S5 session files; never open transcripts.
    native = [p for sid in ids for p in sessions.glob('2026/09/12/*' + sid + '*')]
    checks['synthetic_native_sessions_found'] = len(native)
    checks['synthetic_native_sessions_owner_protected'] = all(
        safe_path(p, private=True) or protected_by_owner_directory(p) for p in native) if native else False
    benchmark_ids = set()
    for metadata in (private / 's6-latency').glob('*/sessions.json'):
        benchmark_ids.update(json.loads(metadata.read_text()).values())
    benchmark = [p for sid in benchmark_ids for p in sessions.glob('2026/09/12/*' + sid + '*')]
    checks['benchmark_native_sessions_found'] = len(benchmark)
    checks['benchmark_native_sessions_owner_only'] = bool(benchmark) and all(safe_path(p, private=True) for p in benchmark)
    return checks


def main():
    try:
        result = audit()
    except Exception:
        result = {'audit_completed': False}
    print(json.dumps(result, sort_keys=True))
    required = [v for k, v in result.items() if type(v) is bool and not k.startswith('synthetic_native_')]
    failed_counts = result.get('ordinary_secret_matches', 0) + result.get('private_files_tracked', 0)
    # Native files belong to Codex. Report their observed access separately;
    # retention is explicitly accepted and Sebastian does not manage that store.
    return 0 if required and all(required) and not failed_counts else 1


if __name__ == '__main__':
    sys.exit(main())
