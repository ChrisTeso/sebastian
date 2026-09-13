"""Final-only, bounded channel formatting; no delivery side effects."""
import re
from .contracts import Channel

FAILURE='I could not finish this request. Please try again.'
PROGRESS=re.compile(r'^(?:completed:\s*)?(?:working|on it|working on it|thinking|in progress)[.!…]*$',re.I)

def format_reply(text:str,channel:Channel,limit:int|None=None)->list[str]:
    if not isinstance(text,str) or not text.strip() or PROGRESS.fullmatch(text.strip()):text=FAILURE
    if '\x00' in text:text=text.replace('\x00','')
    if channel==Channel.SLACK:
        text=re.sub(r'(?m)^#{1,6}\s+(.+)$',r'*\1*',text)
        text=re.sub(r'\*\*([^*\n]+)\*\*',r'*\1*',text)
    limit=limit or (3500 if channel==Channel.SLACK else 1800)
    if limit<100:raise ValueError('Reply limit too small')
    limit-=80  # Reserve room for a closing/reopened code fence.
    parts=[];remaining=text.strip()
    while len(remaining)>limit:
        split=max(remaining.rfind('\n\n',0,limit),remaining.rfind('\n',0,limit),remaining.rfind(' ',0,limit))
        if split<limit//2:split=limit
        parts.append(remaining[:split].rstrip());remaining=remaining[split:].lstrip()
    if remaining:parts.append(remaining)
    balanced=[];fence=None
    for part in parts:
        prefix=(fence+'\n') if fence else ''
        for line in part.splitlines():
            if line.startswith('```'):
                fence=None if fence else line[:60]
        balanced.append(prefix+part+('\n```' if fence else ''))
    return balanced

SIGNATURE="– Sebastian, Chris's AI Assistant"

def signed_reply(text:str,channel:Channel)->list[str]:
    """Host-owned signature on every text part, outside any code fence."""
    text=text.rstrip()
    text=re.sub(r'(?m)^'+re.escape(SIGNATURE)+r'\s*$','',text).rstrip()
    if not text:return [SIGNATURE]
    limit=(3500 if channel==Channel.SLACK else 1800)-len(SIGNATURE)-2
    return [part+'\n\n'+SIGNATURE for part in format_reply(text,channel,limit=limit)]
