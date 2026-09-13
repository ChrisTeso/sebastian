import tempfile,unittest
from pathlib import Path
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
from sebastian.contracts import Channel,Event,AttachmentRef,HistoryMessage
from sebastian.images import ConversationImages,ImageUnavailable,image_data
from sebastian.formatting import format_reply

class ImageFormattingTests(unittest.TestCase):
    def test_formats_split_long_fenced_answer_and_filter_progress(self):
        for channel in Channel:
            parts=format_reply('```python\n'+('print(42)\n'*900)+'```',channel)
            self.assertGreater(len(parts),1)
            self.assertTrue(all(len(p)<=(3500 if channel==Channel.SLACK else 1800) for p in parts))
            self.assertTrue(all(p.count('```')%2==0 for p in parts))
            self.assertNotIn('On it',format_reply('Completed: On it',channel)[0])
    def test_binary_type_and_limits(self):
        self.assertTrue(image_data(b'\x89PNG\r\n\x1a\nfixture').startswith('data:image/png;base64,'))
        with self.assertRaises(ImageUnavailable):image_data(b'<svg>untrusted</svg>')
        with self.assertRaises(ImageUnavailable):image_data(b'x'*8_000_001)
    def test_slack_download_never_accepts_arbitrary_url(self):
        class Client:
            def files_info(self,**kwargs):return {'ok':True,'file':{'id':'F','url_private_download':'https://attacker.example/private'}}
        loader=ConversationImages(type('API',(),{'client':Client()})(),None,'synthetic-token')
        event=Event(Channel.SLACK,'TEAM','USER','GROUP',None,'ID',datetime.now(timezone.utc),'image',attachments=(AttachmentRef('F','image/png',12),))
        with self.assertRaises(ImageUnavailable):loader.load(event)
    def test_no_cross_conversation_attachment_file(self):
        class DB:
            def execute(self,sql,args):
                self.args=args;return self
            def fetchone(self):return None
        db=DB();loader=ConversationImages(None,db,'')
        event=Event(Channel.MESSAGES,'ACCOUNT','USER','EXACTCHAT',None,'EXACTEVENT',datetime.now(timezone.utc),'image',attachments=(AttachmentRef('messages-attachment:A','image/png',12),))
        with self.assertRaises(ImageUnavailable):loader.load(event)
        self.assertEqual(db.args,('A','EXACTEVENT','EXACTCHAT'))

    def test_preceding_images_are_exact_conversation_only(self):
        now=datetime.now(timezone.utc);ref=AttachmentRef('F','image/png',20)
        good=HistoryMessage(Channel.SLACK,'TEAM','OTHER','GROUP','THREAD','BEFORE',now-timedelta(seconds=1),'photo',(ref,))
        bad=HistoryMessage(Channel.SLACK,'OTHERTEAM','OWNER','PRIVATE',None,'PRIVATE',now-timedelta(seconds=1),'private',(ref,))
        event=Event(Channel.SLACK,'TEAM','USER','GROUP','THREAD','NOW',now,'What color is the photo above?',history=(good,bad))
        loader=ConversationImages(None,None,'synthetic')
        with patch.object(loader,'_slack',return_value=b'\x89PNG\r\n\x1a\nfixture') as read:
            self.assertEqual(len(loader.load_context(event)),1)
            self.assertEqual(read.call_args.args[0].event_id,'BEFORE')

    def test_skip_old_images_still_loads_current_attachment(self):
        now=datetime.now(timezone.utc);ref=AttachmentRef('F','image/png',20)
        old=HistoryMessage(Channel.SLACK,'TEAM','USER','GROUP',None,'BEFORE',now-timedelta(seconds=1),'old',(ref,))
        event=Event(Channel.SLACK,'TEAM','USER','GROUP',None,'NOW',now,'What is 7 + 8?',attachments=(ref,),history=(old,))
        loader=ConversationImages(None,None,'synthetic')
        with patch.object(loader,'_slack',return_value=b'\x89PNG\r\n\x1a\nfixture') as read:
            self.assertEqual(len(loader.load_context(event,include_history=False)),1)
            self.assertEqual(read.call_count,1)
            self.assertEqual(read.call_args.args[0].event_id,'NOW')
