import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('privacy', Path(__file__).resolve().parents[1] / 'scripts/check_privacy.py')
privacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(privacy)


class PrivacyTests(unittest.TestCase):
    def test_unsafe_files_and_parent_links_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            p = root / 'token'
            p.write_text('synthetic-only')
            p.chmod(0o600)
            self.assertTrue(privacy.safe_path(p, private=True))
            p.chmod(0o644)
            self.assertFalse(privacy.safe_path(p, private=True))
            p.chmod(0o600)
            os.link(p, root / 'hardlink')
            self.assertFalse(privacy.safe_path(p, private=True))
            (root / 'hardlink').unlink()
            alias = root / 'alias'
            alias.symlink_to(root, target_is_directory=True)
            self.assertFalse(privacy.safe_path(alias / 'token', private=True))
            self.assertFalse(privacy.safe_path(Path('relative')))

    def test_value_scan_reports_only_count(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / 'ordinary'
            p.write_bytes(b'example synthetic-canary-value example')
            self.assertEqual(privacy.count_matches([p], [b'synthetic-canary-value']), 1)
            self.assertEqual(privacy.count_matches([p], [b'absent-value']), 0)

    def test_private_ancestor_protects_native_file(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder).resolve() / 'transcript'
            p.write_text('synthetic-only')
            p.chmod(0o644)
            self.assertTrue(privacy.protected_by_owner_directory(p))
