#!/usr/bin/env python3
"""S6 recovery evidence: real Engine/Ledger, synthetic providers, optional native runtime.

No messages are sent. SIGSTOP targets only the child this probe creates.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sebastian.contracts import Channel, Destination, Event
from sebastian.engine import Engine
from sebastian.ledger import Ledger, JobMetadata
from sebastian.policy import AdapterRegistry, PolicyConfig


class RuntimeFixture:
    def __init__(self): self.calls = 0; self.fail = False
    def stage_images(self, images): pass
    def respond(self, *args):
        self.calls += 1
        if self.fail: raise RuntimeError('SYNTHETIC_PRIVATE_ERROR')
        return 'SYNTHETIC_FINAL_BODY'
    def cancel(self): return True
    def close(self): pass


class SourceFixture:
    def __init__(self, event):
        self.registry = AdapterRegistry()
        self.signer = self.registry.register('fixture', event.channel, event.account_id)
        self.event = event; self.failures = 0; self.fetches = 0; self.sent = []
    def fetch(self, job):
        self.fetches += 1
        if self.failures:
            self.failures -= 1
            raise ConnectionError('SYNTHETIC_PROVIDER_ERROR')
        return self.signer.sign(self.event), []
    def send(self, destination, text):
        self.sent.append((destination, text))
        return 'synthetic-receipt-' + str(len(self.sent))
    def close(self): pass


class Harness:
    def __init__(self, root, *, real_clock=False, stale_after=600):
        self.root = Path(root); self.now = time.time()
        self.clock = time.time if real_clock else lambda: self.now
        self.stale_after = stale_after
        self.event = Event(Channel.SLACK, 'TEAM', 'OWNER', 'GROUP', 'THREAD', 'EVENT',
                           datetime.fromtimestamp(self.clock(), timezone.utc),
                           'SYNTHETIC_PRIVATE_REQUEST', is_group=True)
        self.sources = SourceFixture(self.event); self.runtime = RuntimeFixture()
        self.settings = SimpleNamespace(state_dir=self.root, owner_workspace=self.root,
            restricted_workspace=self.root, policy=PolicyConfig(
                owner_slack=frozenset({('TEAM', 'OWNER')}), owner_group_replies=True,
                owner_destinations=(Destination(Channel.SLACK, 'TEAM', 'DM'),)))
        self.open()
    def open(self):
        self.ledger = Ledger(self.root/'ledger.sqlite', clock=self.clock, stale_after=self.stale_after)
        with patch('sebastian.engine.load_user_config', return_value={}):
            self.engine = Engine(self.settings, self.runtime, ledger=self.ledger, sources=self.sources)
    def accept(self): return self.ledger.accept(JobMetadata.from_event(self.event))
    def restart(self): self.engine.close(); self.open()
    def close(self): self.engine.close()
    def snapshot(self, job):
        item = self.ledger.get_job(job.job_id)
        return dict(status=item.status, retries=item.retries, runtime_calls=self.runtime.calls,
                    fetches=self.sources.fetches, fixture_sends=len(self.sources.sent),
                    deliveries=[d.status for d in self.ledger.deliveries(job.job_id)])


def fixture_matrix(root):
    results = {}
    for case in ('fresh_start', 'temporary_network', 'network_exhausted', 'runtime_failure',
                 'stale_command', 'restart_running', 'restart_partial', 'restart_complete'):
        h = Harness(root/case)
        try:
            assert h.engine.run_once() is False
            job = h.accept()
            if case == 'temporary_network':
                h.sources.failures = 2
                states = []
                for delay in (1, 2):
                    h.engine.run_once(); states.append(h.snapshot(job))
                    assert not h.engine.run_once()
                    h.now += delay
                h.engine.run_once()
                assert h.runtime.calls == 1 and h.sources.fetches == 3
                results[case] = {'retry_states': states, **h.snapshot(job)}
            elif case == 'network_exhausted':
                h.sources.failures = 10
                for delay in (1, 2, 4, 8):
                    h.engine.run_once(); h.now += delay
                assert h.ledger.get_job(job.job_id).status == 'interrupted'
                h.engine.run_once()
                assert h.sources.fetches == 4 and h.runtime.calls == 0
            elif case == 'runtime_failure':
                h.runtime.fail = True; h.engine.run_once(); h.restart(); h.engine.run_once()
                assert h.runtime.calls == 1 and len(h.sources.sent) == 1
                assert 'did not replay' in h.sources.sent[0][1]
            elif case == 'stale_command':
                h.now += 601; h.engine.run_once(); h.engine.run_once()
                assert h.runtime.calls == 0 and h.sources.fetches == 0
            elif case.startswith('restart_'):
                h.ledger.claim(); h.ledger.start(job.job_id)
                if case != 'restart_running':
                    dest = Destination(Channel.SLACK, 'TEAM', 'GROUP', 'THREAD')
                    a = h.ledger.plan(job.job_id, dest, 'SYNTHETIC_PART_A', 0)
                    h.ledger.attempting(a.part_id); h.ledger.confirmed(a.part_id, 'synthetic-A')
                    if case == 'restart_partial':
                        b = h.ledger.plan(job.job_id, dest, 'SYNTHETIC_PART_B', 1)
                        h.ledger.attempting(b.part_id)
                        h.ledger.plan(job.job_id, dest, 'SYNTHETIC_PART_C', 2)
                h.restart()
                recovered = h.snapshot(job)
                h.engine.run_once(); h.engine.run_once()
                assert h.runtime.calls == 0
                if case == 'restart_partial':
                    assert recovered['deliveries'] == ['confirmed', 'uncertain', 'abandoned']
                if case == 'restart_complete': assert h.sources.sent == []
                results[case] = {'after_restart': recovered, **h.snapshot(job)}
            else:
                h.engine.run_once(); h.accept(); h.restart(); h.engine.run_once()
                assert h.runtime.calls == 1 and len(h.sources.sent) == 1
            results.setdefault(case, h.snapshot(job))
            assert results[case]['status'] == 'complete'
            assert not h.engine.run_once()
            for dest, _ in h.sources.sent:
                assert dest == Destination(Channel.SLACK, 'TEAM', 'GROUP', 'THREAD')
        finally: h.close()
        for path in (root/case).iterdir():
            assert b'SYNTHETIC_PRIVATE' not in path.read_bytes()
            assert b'SYNTHETIC_FINAL_BODY' not in path.read_bytes()
            assert path.stat().st_mode & 0o777 == 0o600
    return results


def paused_child(root):
    h = Harness(root, real_clock=True, stale_after=1)
    try:
        job = h.accept()
        print('ready', flush=True)
        sys.stdin.readline()
        h.engine.run_once(); h.engine.run_once()
        result = h.snapshot(job)
        assert result['runtime_calls'] == 0 and result['status'] == 'complete'
        print(json.dumps(result), flush=True)
    finally: h.close()


def process_suspend(root):
    child = subprocess.Popen([sys.executable, __file__, '--paused-child', str(root)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        import select
        assert select.select([child.stdout], [], [], 10)[0], 'child startup timeout'
        assert child.stdout.readline().strip() == 'ready'
        started = time.monotonic()
        os.kill(child.pid, signal.SIGSTOP)
        pid, status = os.waitpid(child.pid, os.WUNTRACED)
        assert pid == child.pid and os.WIFSTOPPED(status)
        time.sleep(1.25)
        os.kill(child.pid, signal.SIGCONT)
        output, _ = child.communicate('\n', timeout=10)
        assert child.returncode == 0
        return {'kind': 'owned_process_suspend_not_physical_mac_sleep',
                'stopped_observed': True, 'elapsed_s': time.monotonic()-started,
                **json.loads(output)}
    finally:
        if child.poll() is None:
            os.kill(child.pid, signal.SIGCONT); child.kill(); child.wait(timeout=10)


def native_matrix(root):
    from sebastian.session_runtime import SessionRuntime
    from sebastian.runtime_policy import make_runtime_policy, load_user_config
    profile = make_runtime_policy('conversation', Path('/tmp'), load_user_config())
    runtime = SessionRuntime(root/'sessions.json')
    result = {}
    try:
        marker = 'cerulean-83419'
        runtime.respond('s6-recovery', profile, 'Remember the synthetic token '+marker+'. Reply OK.', timeout=90)
        result['fresh_request_completed'] = True
        answer = runtime.respond('s6-recovery', profile, 'Repeat the synthetic token.', timeout=90)
        result['warm_continuity'] = marker in answer
        process = runtime._connection.process
        process.kill(); process.wait(timeout=10)
        result['owned_runtime_killed'] = process.returncode is not None
        answer = runtime.respond('s6-recovery', profile, 'Repeat the synthetic token.', timeout=90)
        result['executor_reconnect_continuity'] = marker in answer
        runtime.close(); runtime = SessionRuntime(root/'sessions.json')
        answer = runtime.respond('s6-recovery', profile, 'Repeat the synthetic token.', timeout=90)
        result['runtime_restart_continuity'] = marker in answer
        assert all(result.values())
        from sebastian.toolbelt_reader import ToolbeltReader
        fixture = root/'approved.md'
        fd = os.open(fixture, os.O_CREAT|os.O_EXCL|os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w') as stream: stream.write('Synthetic recovery code: BRONZE-OTTER-285194.')
        reader = ToolbeltReader(root.resolve(), {'sample': 'approved.md'})
        tool_profile = make_runtime_policy('toolbelt_read_only', root.resolve(), load_user_config())
        started = time.monotonic()
        answer = runtime.respond('s6-local-tool', tool_profile,
            'Use toolbelt_read with document_id sample. Return its synthetic recovery code. Do not guess.',
            reader, timeout=90)
        result['local_tool_request'] = {'answer_verified': 'BRONZE-OTTER-285194' in answer,
            'elapsed_s': time.monotonic()-started, 'timings': dict(runtime.last_timings),
            'scope': 'one synthetic allowlisted local document read; not representative of tool-heavy tasks'}
        assert result['local_tool_request']['answer_verified']
    finally: runtime.close()
    result['owned_process_cleaned'] = runtime._connection is None or runtime._connection.process.poll() is not None
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--native', action='store_true')
    parser.add_argument('--paused-child', type=Path)
    args = parser.parse_args()
    if args.paused_child: paused_child(args.paused_child); return
    root = Path('.private/s6-recovery'); root.mkdir(mode=0o700, parents=True, exist_ok=True); root.chmod(0o700)
    run = Path(tempfile.mkdtemp(prefix='run-', dir=root))
    evidence = {'fixture_matrix': fixture_matrix(run), 'process_suspend': process_suspend(run/'suspend'),
                'physical_mac_sleep_wake': 'NOT_OBSERVED'}
    if args.native: evidence['native_runtime'] = native_matrix(run/'native')
    target = run/'results.json'
    fd = os.open(target, os.O_CREAT|os.O_EXCL|os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as stream: json.dump(evidence, stream, indent=2)
    print(json.dumps({'evidence_path': str(target), **evidence}, indent=2))


if __name__ == '__main__': main()
