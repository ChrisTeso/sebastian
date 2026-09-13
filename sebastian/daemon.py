"""Private single-instance host for the verified engine; no channel content logs."""
from __future__ import annotations
import argparse
from contextlib import closing
import fcntl
import json
import logging
import os
from pathlib import Path
import signal
import sqlite3
import stat
import tempfile
import threading
import time


TIMING_KEYS = ('queue_wait_s', 'ingress_fetch_s', 'model_tools_s', 'runtime_startup_s', 'delivery_s', 'total_s')


class AlreadyRunning(RuntimeError):
    pass


def private_directory(path: Path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise PermissionError('Unsafe service directory')
    os.chmod(path, 0o700)


class InstanceLock:
    """Never unlink the lock inode: another process may already have opened it."""
    def __init__(self, path: Path):
        self.fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(self.fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                raise PermissionError('Unsafe service lock')
            os.fchmod(self.fd, 0o600)
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise AlreadyRunning('Service already running') from None
        except BaseException:
            os.close(self.fd)
            raise

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def write_status(path: Path, data: dict):
    fd, name = tempfile.mkstemp(prefix='.health-', dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def health(engine, settings, phase='running', failure=None):
    readable = False
    try:
        with closing(sqlite3.connect(settings.messages_db.as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
            db.execute('SELECT ROWID FROM message LIMIT 1').fetchone()
        readable = True
    except Exception:
        pass
    connected = False
    try:
        connected = bool(engine.sources.client.is_connected()) if engine else False
    except Exception:
        pass
    connection = getattr(getattr(engine, 'runtime', None), '_connection', None)
    process = getattr(connection, 'process', None)
    runtime_pid = process.pid if process is not None and process.poll() is None else None
    # Never persist exception strings or arbitrary engine diagnostics.
    last_failure = failure or ('engine_failure' if engine and engine.last_failure else None)
    return dict(version=1, phase=phase, pid=os.getpid(), heartbeat_at=time.time(),
                slack_connected=connected, messages_readable=readable,
                runtime_pid=runtime_pid, last_failure=last_failure,
                last_timings={k: v for k, v in getattr(engine, 'last_timings', {}).copy().items()
                              if k in TIMING_KEYS and type(v) in (int, float) and 0 <= v < 1e9})


def stop_handler(engine):
    def stop(signum, frame):
        engine.stopped.set()
        engine.runtime.cancel()
    return stop


def run(config: Path):
    # Set before loading settings, creating state, or starting native children.
    os.umask(0o077)
    logging.disable(logging.CRITICAL)
    from .config import load_settings
    from .engine import Engine
    from .routed_runtime import RoutedRuntime
    settings = load_settings(config)
    private_directory(settings.state_dir)
    lock = InstanceLock(settings.state_dir / 'service.lock')
    engine = runtime = None
    monitor = None
    monitor_stop = threading.Event()
    prior = {}
    status_path = settings.state_dir / 'service-status.json'
    failure = None
    try:
        write_status(status_path, health(None, settings, 'starting'))
        runtime = RoutedRuntime(settings.state_dir / 'sessions.json', settings.restricted_workspace)
        engine = Engine(settings, runtime)
        handler = stop_handler(engine)
        for sig in (signal.SIGTERM, signal.SIGINT):
            prior[sig] = signal.signal(sig, handler)
        launcher_pid = int(os.environ.get('SEBASTIAN_LAUNCHER_PID', '0'))
        def heartbeat():
            while not monitor_stop.is_set():
                try:
                    if launcher_pid and os.getppid() != launcher_pid:
                        engine.stopped.set()
                        runtime.cancel()
                        return
                    write_status(status_path, health(engine, settings))
                except Exception:
                    # An unwritable health file must not leave an invisible service running.
                    engine.stopped.set()
                    runtime.cancel()
                    return
                monitor_stop.wait(5)
        monitor = threading.Thread(target=heartbeat, name='sebastian-health', daemon=True)
        monitor.start()
        engine.run_forever()
    except Exception:
        failure = 'service_failure'
        return 1
    finally:
        monitor_stop.set()
        if monitor:
            monitor.join(timeout=5)
        try:
            if engine:
                engine.close()
            elif runtime:
                runtime.close()
        finally:
            try:
                write_status(status_path, health(None, settings, 'stopped', failure))
            finally:
                for sig, previous in prior.items():
                    signal.signal(sig, previous)
                lock.close()
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    try:
        return run(args.config)
    except AlreadyRunning:
        return 2
    except Exception:
        # Startup may involve credentials/provider diagnostics; never print traceback.
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
