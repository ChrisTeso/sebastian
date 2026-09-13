"""Outbound image fixtures: no provider calls or live Messages sends."""
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from sebastian.contracts import Channel, Destination
from sebastian.messages import MessagesSender
from sebastian.slack import SlackAPI, SlackConfig


class ImageTransportTests(unittest.TestCase):
    def slack(self, result):
        calls = []
        client = SimpleNamespace(files_upload_v2=lambda **kw: (calls.append(kw) or result))
        api = SlackAPI(client, SlackConfig('T1', 'BOT', 'B1', 'A1', 'OWNER', datetime.now(timezone.utc)))
        api.verified = True
        return api, calls

    def image(self):
        return SimpleNamespace(data=b'validated-fixture', media_type='image/png', filename='image.png')

    def test_slack_upload_uses_exact_channel_thread_and_bytes(self):
        api, calls = self.slack({'ok': True, 'files': [{'id': 'F123'}]})
        dest = Destination(Channel.SLACK, 'T1', 'C1', '200.000001')
        self.assertEqual(api.send_image(dest, self.image()), 'F123')
        self.assertEqual(calls, [dict(channel='C1', thread_ts='200.000001', file=b'validated-fixture',
                                      filename='image.png', title='image.png')])

    def test_slack_identity_failure_does_not_upload(self):
        api, calls = self.slack({'ok': True, 'files': [{'id': 'F123'}]})
        for dest in (Destination(Channel.SLACK, 'OTHER', 'D1'), Destination(Channel.MESSAGES, 'T1', 'chat')):
            with self.assertRaises(PermissionError):
                api.send_image(dest, self.image())
        api.verified = False
        with self.assertRaises(PermissionError):
            api.send_image(Destination(Channel.SLACK, 'T1', 'D1'), self.image())
        self.assertEqual(calls, [])

    def test_slack_rejects_unconfirmed_and_redacts_errors(self):
        dest = Destination(Channel.SLACK, 'T1', 'D1')
        for result in ({'ok': False}, {'ok': True}, {'ok': True, 'files': []},
                       {'ok': True, 'files': [{'id': 'bad'}]}):
            api, _ = self.slack(result)
            with self.assertRaisesRegex(RuntimeError, 'unconfirmed'):
                api.send_image(dest, self.image())
        api.client.files_upload_v2 = lambda **kw: (_ for _ in ()).throw(RuntimeError('private-url'))
        with self.assertRaisesRegex(RuntimeError, 'uncertain') as error:
            api.send_image(dest, self.image())
        self.assertNotIn('private-url', str(error.exception))

    def test_slack_image_bounds_and_filename(self):
        api, calls = self.slack({})
        for field, value in [('data', b''), ('data', b'x' * 20_000_001), ('media_type', 'text/html'),
                             ('filename', '../private.png'), ('filename', 'evil\n.png')]:
            image = self.image()
            setattr(image, field, value)
            with self.assertRaises(ValueError):
                api.send_image(Destination(Channel.SLACK, 'T1', 'D1'), image)
        self.assertEqual(calls, [])

    def test_messages_private_path_argv_and_lifetime(self):
        calls = []
        sender = MessagesSender('account', run=lambda command, **kw: (calls.append(command) or SimpleNamespace(returncode=0)))
        dest = Destination(Channel.MESSAGES, 'account', 'SMS;+;fixture')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'quote " image.png'
            path.write_bytes(b'validated-fixture')
            path.chmod(0o600)
            sender.send_image(dest, path)
            self.assertEqual(calls[0][-2:], [dest.conversation_id, str(path)])
            self.assertNotIn(str(path), calls[0][2])
            self.assertIn('send imageFile to targetChat', calls[0][2])
            self.assertTrue(path.exists())  # caller retains for async receipt
            for bad in [Destination(Channel.MESSAGES, 'other', 'chat'), Destination(Channel.SLACK, 'account', 'D1')]:
                with self.assertRaises(ValueError):
                    sender.send_image(bad, path)
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                sender.send_image(dest, path)
            path.chmod(0o600)
            link = Path(directory) / 'link.png'
            link.symlink_to(path)
            with self.assertRaises(ValueError):
                sender.send_image(dest, link)
            self.assertEqual(len(calls), 1)

    def test_messages_uncertain_error_is_redacted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'image.png'
            path.write_bytes(b'fixture')
            path.chmod(0o600)
            dest = Destination(Channel.MESSAGES, 'account', 'chat')
            for failure in (subprocess.TimeoutExpired('private', 30), OSError('private')):
                def run(*args, **kwargs):
                    raise failure
                with self.assertRaisesRegex(RuntimeError, 'uncertain') as error:
                    MessagesSender('account', run=run).send_image(dest, path)
                self.assertNotIn('private', str(error.exception))
            with self.assertRaisesRegex(RuntimeError, 'uncertain'):
                MessagesSender('account', run=lambda *a, **k: SimpleNamespace(returncode=1)).send_image(dest, path)


if __name__ == '__main__':
    unittest.main()
