"""Conservative scheduling hints; authorization remains in Policy/RuntimePolicy."""
import re

IMAGE_START=re.compile(r'^(?:please\s+)?(?:generate|create|draw|render|make|respond with|reply with)\b.{0,45}\b(?:image|picture|illustration|icon|portrait|logo|photo)\b',re.I|re.S)
DEPENDENCY=re.compile(r'https?://|[/\\]|\b(?:desktop|file|folder|slack|email|calendar|browser|screen|website|document|download|upload|send|post|message|find|search|read|open|use|attached|attachment|this|that|previous|last|again|edit|modify|change|my|remind|schedule|alarm|notify|book|reserve|purchase|delete|transfer|and|then|also|after|before)\b',re.I)
DEEP=re.compile(r'\b(?:analy[sz]e|audit|debug|investigate|design|architect|review|plan|research|compare|implement|build|fix|deploy|code|legal|medical|investment|diagnos\w*|complex|thorough|careful|reason|explain)\b',re.I)

def request_text(text):
    return re.sub(r'^\s*(?:<@[A-Z0-9]+>|@sebastian)\s*[:,]?\s*','',text,flags=re.I).strip()

def request_lane(text,*,owner,attachments=False):
    text=request_text(text)
    if owner and not attachments and len(text)<=2000 and IMAGE_START.search(text) and not DEPENDENCY.search(text) and not re.search(r'[;\n]|[.!?]\s+\S',text):return 'image'
    return 'normal'

def reasoning_effort(text):
    # Preserve the previous reasoning depth except for a narrow routine set.
    text=request_text(text)
    routine=re.match(r"^(?:(?:hi|hey|hello|thanks|thank you|good morning|good evening)[!. ]*$|(?:what is|what's|calculate)\s+\d|(?:reply|respond|return)\s+(?:with\s+)?only\b|(?:translate|proofread|rewrite|rephrase)\b)",text,re.I)
    return 'low' if len(text)<=500 and routine and not DEEP.search(text) else 'medium'

def needs_image_history(text):
    # Current attachments always load. Only self-contained greetings, numeric
    # expressions and explicit literal responses can omit old image pixels.
    text=request_text(text)
    greeting=re.fullmatch(r'(?:hi|hey|hello|thanks|thank you|good morning|good evening)[!. ]*',text,re.I)
    literal=re.fullmatch(r'(?i:(?:reply|respond|return)\s+(?:with\s+)?only)\s+(?:OK|ACK|PONG|[A-Z]+[-_:][A-Z0-9_:-]+)[.!]?',text)
    arithmetic=re.fullmatch(r"(?:what is|what's|calculate)\s+\d[\d\s+*/().%×÷-]*(?:\?|=)?(?:\s*(?:reply|respond|return)\s+(?:with\s+)?only\s+[A-Za-z0-9_:-]+[.!]?)?",text,re.I)
    return not (len(text)<=500 and (greeting or literal or arithmetic))
