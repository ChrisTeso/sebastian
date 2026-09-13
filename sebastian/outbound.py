"""Bounded generated reply images. Bytes remain in memory until transport."""
import base64,binascii,re,uuid
from dataclasses import dataclass,field
from pathlib import Path
from .images import image_data,MAX_IMAGE_BYTES,ImageUnavailable

MAX_REPLY_IMAGES=4

@dataclass(frozen=True)
class OutboundImage:
    data:bytes=field(repr=False)
    media_type:str
    filename:str=field(default='',repr=False)
    def __post_init__(self):
        actual=image_data(self.data).split(';',1)[0].removeprefix('data:')
        if actual!=self.media_type:raise ImageUnavailable('Image format does not match its content')
        ext={'image/png':'png','image/jpeg':'jpg','image/gif':'gif','image/webp':'webp'}[actual]
        if not self.filename:object.__setattr__(self,'filename',f'sebastian-{uuid.uuid4().hex}.{ext}')
        if not re.fullmatch(r'sebastian-[a-f0-9]{32}\.(png|jpg|gif|webp)',self.filename):raise ValueError('Invalid image filename')
    @property
    def marker(self):return image_marker(self.filename)
    @classmethod
    def from_result(cls,result):
        if not isinstance(result,str) or not result or len(result)>4*((MAX_IMAGE_BYTES+2)//3)+100:raise ImageUnavailable('Generated image exceeds the size limit')
        encoded=result.split(',',1)[1] if result.startswith('data:image/') and ',' in result else result
        try:data=base64.b64decode(encoded,validate=True)
        except (ValueError,binascii.Error):raise ImageUnavailable('Invalid generated image') from None
        mime=image_data(data).split(';',1)[0].removeprefix('data:')
        return cls(data,mime)

def image_marker(filename):
    # Messages may change an image extension while transcoding for a carrier.
    stem=Path(filename).stem
    return '\0sebastian-image:'+stem if re.fullmatch(r'sebastian-[a-f0-9]{32}',stem) else None

@dataclass(frozen=True)
class RuntimeReply:
    text:str=field(default='',repr=False)
    images:tuple[OutboundImage,...]=field(default=(),repr=False)
    model:str|None=None
    routed_by:str|None=None
    def __post_init__(self):
        if not isinstance(self.text,str) or len(self.images)>MAX_REPLY_IMAGES or any(not isinstance(i,OutboundImage) for i in self.images):raise ValueError('Invalid runtime reply')
