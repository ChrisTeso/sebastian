import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('probe_recovery', Path(__file__).resolve().parents[1]/'scripts/probe_recovery.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class RecoveryTests(unittest.TestCase):
    def test_engine_ledger_recovery_matrix(self):
        with tempfile.TemporaryDirectory() as root:
            result = probe.fixture_matrix(Path(root))
        self.assertEqual(len(result), 8)
        self.assertEqual(result['network_exhausted']['retries'], 3)
        self.assertEqual(result['restart_partial']['deliveries'],
                         ['confirmed', 'uncertain', 'abandoned', 'confirmed'])

    def test_owned_process_pause_expires_queued_action(self):
        with tempfile.TemporaryDirectory() as root:
            result = probe.process_suspend(Path(root))
        self.assertTrue(result['stopped_observed'])
        self.assertEqual(result['runtime_calls'], 0)
        self.assertEqual(result['fetches'], 0)


if __name__ == '__main__': unittest.main()
