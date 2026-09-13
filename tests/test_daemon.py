from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import signal
import sqlite3
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from sebastian.daemon import AlreadyRunning, InstanceLock, health, stop_handler, write_status, run

spec = importlib.util.spec_from_file_location('service_control', Path(__file__).resolve().parents[1] / 'scripts/service.py')
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


class DaemonTests(unittest.TestCase):
    def test_singleton_survives_contender_and_releases(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'lock'
            first = InstanceLock(path)
            with self.assertRaises(AlreadyRunning):
                InstanceLock(path)
            first.close()
            second = InstanceLock(path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            second.close()

    def test_lock_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'target'
            target.write_text('untouched')
            link = Path(folder) / 'lock'
            link.symlink_to(target)
            with self.assertRaises(OSError):
                InstanceLock(link)
            self.assertEqual(target.read_text(), 'untouched')

    def test_stop_only_signals_engine_and_cancels(self):
        engine = SimpleNamespace(stopped=threading.Event(), runtime=Mock())
        stop_handler(engine)(signal.SIGTERM, None)
        self.assertTrue(engine.stopped.is_set())
        engine.runtime.cancel.assert_called_once_with()

    def test_health_only_metadata_and_read_only_messages(self):
        with tempfile.TemporaryDirectory() as folder:
            dbpath = Path(folder) / 'messages.db'
            with closing(sqlite3.connect(dbpath)) as db:
                db.execute('CREATE TABLE message(text TEXT)')
                db.execute("INSERT INTO message VALUES ('private fixture')")
                db.commit()
            before = dbpath.read_bytes()
            engine = SimpleNamespace(sources=SimpleNamespace(client=SimpleNamespace(is_connected=lambda: True)),
                                     runtime=None, last_failure='secret provider error',
                                     last_timings={'model_tools_s': 1.3, 'private text': 'secret'})
            data = health(engine, SimpleNamespace(messages_db=dbpath))
            self.assertTrue(data['messages_readable'])
            self.assertTrue(data['slack_connected'])
            self.assertEqual(data['last_failure'], 'engine_failure')
            self.assertEqual(data['last_timings'], {'model_tools_s': 1.3})
            self.assertEqual(before, dbpath.read_bytes())
            path = Path(folder) / 'status'
            write_status(path, data)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn('secret', path.read_text())
            self.assertNotIn('private fixture', path.read_text())

    def test_run_sets_private_umask_and_closes_resources(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = SimpleNamespace(state_dir=Path(folder), messages_db=Path(folder) / 'absent', restricted_workspace=Path(folder))
            runtime = Mock()
            runtime._connection = None
            engine = Mock()
            engine.last_failure = None
            engine.last_timings = {}
            engine.runtime = runtime
            seen = []
            def loaded(path):
                mask = os.umask(0o077)
                seen.append(mask)
                return settings
            with patch('sebastian.config.load_settings', side_effect=loaded), patch('sebastian.engine.Engine', return_value=engine), patch('sebastian.routed_runtime.RoutedRuntime', return_value=runtime):
                self.assertEqual(run(Path(folder) / 'config'), 0)
            self.assertEqual(seen, [0o077])
            engine.close.assert_called_once_with()
            self.assertEqual(json.loads((Path(folder) / 'service-status.json').read_text())['phase'], 'stopped')
            lock = InstanceLock(Path(folder) / 'service.lock')
            lock.close()

    def test_launchagent_is_scoped_and_private(self):
        plist = control.definition()
        self.assertEqual(plist['Label'], 'com.christeso.sebastian.native')
        self.assertEqual(plist['Umask'], 0o077)
        self.assertTrue(plist['KeepAlive'])
        self.assertEqual(plist['StandardOutPath'], '/dev/null')
        self.assertEqual(plist['StandardErrorPath'], '/dev/null')
        self.assertEqual(plist['ProgramArguments'], ['/usr/bin/open', '-W', '-n', str(control.APP)])
        self.assertEqual(set(plist['EnvironmentVariables']), {'HOME', 'PATH'})

    def test_stop_disables_before_bootout_and_never_kills_pid(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(control, 'APP', Path(folder) / 'Absent.app'), patch.object(control, 'launchctl', return_value=0) as launch, patch('sys.argv', ['service.py', 'stop']):
            self.assertEqual(control.main(), 0)
        self.assertEqual(launch.call_args_list[0].args, ('disable', control.TARGET))
        self.assertEqual(launch.call_args_list[-1].args, ('bootout', control.TARGET))

    def test_stop_waits_for_exact_new_app_after_unloading(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Path(folder) / 'Sebastian Native.app'
            executable = app / 'Contents/MacOS/Sebastian Native'
            executable.parent.mkdir(parents=True)
            executable.touch()
            events = []
            def launch(*args):
                events.append(args)
                return 0
            def stop(command, **kwargs):
                events.append(tuple(command))
                return SimpleNamespace(returncode=0)
            with patch.object(control, 'APP', app), patch.object(control, 'launchctl', side_effect=launch), patch.object(control.subprocess, 'run', side_effect=stop), patch('sys.argv', ['service.py', 'stop']):
                self.assertEqual(control.main(), 0)
            self.assertEqual(events[-2:], [('bootout', control.TARGET), (str(executable), '--stop')])

    def test_install_does_not_activate_or_overwrite_state(self):
        with tempfile.TemporaryDirectory() as folder:
            plist = Path(folder) / 'service.plist'
            with patch.object(control, 'PLIST', plist), patch.object(control, 'load_settings'), patch.object(control, 'build_app') as build, patch.object(control, 'launchctl') as launch:
                control.install()
            launch.assert_not_called()
            build.assert_called_once_with()
            self.assertEqual(plist.stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
