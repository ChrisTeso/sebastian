#!/usr/bin/env python3
"""Authorized physical sleep/wake check; no channel sends or power preference edits."""
import json, os, subprocess, sys, tempfile, time
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.probe_recovery import Harness
from sebastian.session_runtime import SessionRuntime
from sebastian.runtime_policy import make_runtime_policy, load_user_config

os.umask(0o077)
resume = len(sys.argv) == 3 and sys.argv[1] == '--resume-evidence'
root = Path(sys.argv[2]).resolve() if resume else Path(tempfile.mkdtemp(prefix='physical-', dir=Path(__file__).resolve().parents[1]/'.private/s6-recovery'))
if not root.is_relative_to(Path(__file__).resolve().parents[1]/'.private/s6-recovery'):
    raise ValueError('Evidence must be in the private recovery directory')
runtime = SessionRuntime(root/'sessions.json')
profile = make_runtime_policy('conversation', root, load_user_config())
result = {'physical_sleep_observed': False, 'physical_wake_observed': False}
harness = None
try:
    marker = 'synthetic-sleep-739126'
    if not resume:
        runtime.respond('physical-sleep', profile, 'Remember this synthetic token: '+marker+'. Reply OK.', timeout=120)
    harness = Harness(root/'queue', real_clock=True, stale_after=10)
    job = harness.accept()
    started = datetime.fromtimestamp(root.stat().st_birthtime) if resume else datetime.now()
    wall = time.time(); monotonic = time.monotonic()
    result['observation_started_at'] = started.isoformat()
    print('Checking existing sleep evidence.' if resume else 'Ready; requesting macOS approval to schedule wake and sleep.', flush=True)
    command = 'do shell script "/usr/bin/pmset relative wake 60 && /usr/bin/pmset sleepnow" with administrator privileges'
    operation = None if resume else subprocess.run(['/usr/bin/osascript', '-e', command], capture_output=True, timeout=180)
    result['power_command_succeeded'] = json.loads((root/'results.json').read_text()).get('power_command_succeeded', False) if resume else operation.returncode == 0
    if not result['power_command_succeeded']:
        result['failure'] = 'macOS did not authorize or complete the power command'
    else:
        # pmset may return before suspension; wait beyond the requested wake time.
        wait_started = time.time()
        while not resume and time.time()-wait_started < 75:
            time.sleep(1)
        log = subprocess.run(['/usr/bin/pmset', '-g', 'log'], capture_output=True, timeout=30).stdout.decode('utf-8', 'replace')
        events = []
        for line in log.splitlines():
            try: stamp = datetime.strptime(line[:19], '%Y-%m-%d %H:%M:%S')
            except ValueError: continue
            if stamp < started.replace(microsecond=0): continue
            if 'Entering Sleep state' in line:
                result['physical_sleep_observed'] = True; events.append({'type':'sleep','at':line[:19]})
            if 'Wake from' in line or 'DarkWake from' in line:
                result['physical_wake_observed'] = True; events.append({'type':'wake','at':line[:19]})
        result['os_power_events'] = events
        result['verification_wall_elapsed_s'] = time.time()-wall
        result['verification_monotonic_elapsed_s'] = time.monotonic()-monotonic
        harness.engine.run_once(); harness.engine.run_once()
        result['stale_queue'] = harness.snapshot(job)
        result['stale_action_not_executed'] = harness.runtime.calls == 0 and harness.sources.fetches == 0 and result['stale_queue']['status'] == 'complete'
        answer = runtime.respond('physical-sleep', profile, 'What exact synthetic token did I ask you to remember? Reply only that token.', timeout=120)
        result['native_session_continuity'] = marker in answer
        result['post_wake_runtime_timings'] = runtime.last_timings
        result['pass'] = all(result.get(k) for k in ('physical_sleep_observed','physical_wake_observed','stale_action_not_executed','native_session_continuity'))
finally:
    if harness: harness.close()
    runtime.close()
    (root/'results.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result), flush=True)
    print('Evidence: '+str(root/'results.json'), flush=True)
sys.exit(0 if result.get('pass') else 1)
