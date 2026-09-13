"""Bounded image loading from references in the authenticated conversation only."""
import base64,os,stat,urllib.request,urllib.parse
from pathlib import Path
from .contracts import Channel,Event

MAX_IMAGE_BYTES=8_000_000
class ImageUnavailable(RuntimeError):pass

def image_data(data:bytes)->str:
    if len(data)>MAX_IMAGE_BYTES:raise ImageUnavailable('Image exceeds the size limit')
    if data.startswith(b'\x89PNG\r\n\x1a\n'):mime='image/png'
    elif data.startswith(b'\xff\xd8\xff'):mime='image/jpeg'
    elif data.startswith((b'GIF87a',b'GIF89a')):mime='image/gif'
    elif data.startswith(b'RIFF') and data[8:12]==b'WEBP':mime='image/webp'
    else:raise ImageUnavailable('Unsupported image format')
    return 'data:'+mime+';base64,'+base64.b64encode(data).decode('ascii')

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ImageUnavailable('Image download redirect requires review')

class ConversationImages:
    def __init__(self,slack_api,messages_db,slack_token:str,attachment_root:Path|None=None):
        self.slack_api=slack_api;self.messages_db=messages_db;self.slack_token=slack_token
        self.root=(attachment_root or Path.home()/'Library/Messages/Attachments').resolve()
    def _slack(self,event:Event,reference:str)->bytes:
        data=self.slack_api.client.files_info(file=reference)
        f=data.get('file',{})
        if not data.get('ok') or f.get('id')!=reference:raise ImageUnavailable('Conversation image is unavailable')
        url=f.get('url_private_download') or f.get('url_private') or ''
        parsed=urllib.parse.urlparse(url)
        host=parsed.hostname or ''
        if parsed.scheme!='https' or parsed.username or parsed.password or parsed.port not in (None,443) or not (host=='slack.com' or host.endswith('.slack.com') or host=='slack-files.com' or host.endswith('.slack-files.com')):
            raise ImageUnavailable('Unexpected image download location')
        req=urllib.request.Request(url,headers={'Authorization':'Bearer '+self.slack_token})
        try:
            with urllib.request.build_opener(NoRedirect()).open(req,timeout=20) as response:return response.read(MAX_IMAGE_BYTES+1)
        except Exception:raise ImageUnavailable('Conversation image could not be downloaded') from None
    def _messages(self,event:Event,reference:str)->bytes:
        guid=reference.removeprefix('messages-attachment:')
        row=self.messages_db.execute('''SELECT a.filename FROM attachment a
          JOIN message_attachment_join ma ON ma.attachment_id=a.ROWID
          JOIN message m ON m.ROWID=ma.message_id
          JOIN chat_message_join cm ON cm.message_id=m.ROWID
          JOIN chat c ON c.ROWID=cm.chat_id
          WHERE a.guid=? AND m.guid=? AND c.guid=? LIMIT 1''',(guid,event.event_id,event.conversation_id)).fetchone()
        if not row or not row[0]:raise ImageUnavailable('Conversation image is unavailable')
        path=Path(row[0]).expanduser()
        try:parts=path.relative_to(self.root).parts
        except ValueError:raise ImageUnavailable('Attachment is outside the approved image directory') from None
        if not parts or any(p in {'.','..'} for p in parts):raise ImageUnavailable('Invalid attachment path')
        fd=os.open(self.root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            for part in parts[:-1]:
                nxt=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=nxt
            imagefd=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
            try:
                info=os.fstat(imagefd)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>MAX_IMAGE_BYTES:raise ImageUnavailable('Attachment cannot be safely opened')
                return os.read(imagefd,MAX_IMAGE_BYTES+1)
            finally:os.close(imagefd)
        except OSError:raise ImageUnavailable('Conversation image is unavailable') from None
        finally:os.close(fd)
    def load(self,event:Event)->list[str]:
        images=[]
        for ref in event.attachments:
            if not ref.media_type.startswith('image/'):continue
            if len(images)>=4:break
            if ref.size_bytes>MAX_IMAGE_BYTES:raise ImageUnavailable('Image exceeds the size limit')
            data=self._slack(event,ref.reference) if event.channel==Channel.SLACK else self._messages(event,ref.reference)
            images.append(image_data(data))
        return images

    def load_context(self,event:Event,*,include_history=True)->list[str]:
        images=self.load(event)
        if not include_history:return images
        for prior in reversed(event.history[-10:]):
            if len(images)>=4:break
            if (prior.channel,prior.account_id,prior.conversation_id,prior.thread_id)!=(event.channel,event.account_id,event.conversation_id,event.thread_id) or prior.occurred_at>event.occurred_at or prior.event_id==event.event_id:continue
            images.extend(self.load(prior)[:4-len(images)])
        return images[:4]
