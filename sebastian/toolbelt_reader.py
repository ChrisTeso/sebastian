"""Curated read-only document tool. No arbitrary filesystem or shell interface."""
from __future__ import annotations
import os,re,stat
from pathlib import Path,PurePosixPath
from .runtime_policy import PermissionDenied

MAX_BYTES=100_000
SECRET=re.compile(r'(?:sk-(?:proj-|env-)?[A-Za-z0-9_-]{24,}|xox[baprs]-[A-Za-z0-9-]{12,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|AKIA[A-Z0-9]{16}|(?:api[_-]?key|password|secret|access[_-]?token)\s*[:=]\s*[\"\'][^\"\']{8,}[\"\'])',re.I)
BLOCKED=re.compile(r'(?:^\.|secret|credential|token|password|node_modules|__pycache__)',re.I)

class ToolbeltReader:
    def __init__(self,root:Path,documents:dict[str,str]):
        if not root.is_absolute() or root.is_symlink() or not root.is_dir():raise ValueError('An approved existing real root is required')
        self.root=root.resolve();self.documents=dict(documents)
        for key,relative in self.documents.items():
            p=PurePosixPath(relative)
            if not key or p.is_absolute() or not p.parts or any(x in {'.','..'} or BLOCKED.search(x) for x in p.parts):raise ValueError('Invalid approved document mapping')
            if p.suffix.lower() not in {'.md','.txt','.rst'}:raise ValueError('Only reviewed text documents may be exposed')
    def descriptor(self)->dict:
        return {'type':'function','name':'toolbelt_read','description':'Read one explicitly approved Toolbelt document. Available document IDs: '+', '.join(sorted(self.documents)), 'inputSchema':{'type':'object','properties':{'document_id':{'type':'string','enum':sorted(self.documents)}},'required':['document_id'],'additionalProperties':False}}
    def read(self,arguments:dict)->str:
        if set(arguments)!={'document_id'} or not isinstance(arguments['document_id'],str):raise PermissionDenied('Invalid read request')
        relative=self.documents.get(arguments['document_id'])
        if relative is None:raise PermissionDenied('Document is not approved')
        parts=PurePosixPath(relative).parts;fd=os.open(self.root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            for part in parts[:-1]:
                nxt=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=nxt
            filefd=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
            try:
                info=os.fstat(filefd)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>MAX_BYTES:raise PermissionDenied('Document cannot be safely exposed')
                content=os.read(filefd,MAX_BYTES+1)
                if len(content)>MAX_BYTES:raise PermissionDenied('Document too large')
                text=content.decode('utf-8')
                if SECRET.search(text):raise PermissionDenied('Document contains credential-like material')
                return text
            finally:os.close(filefd)
        except (OSError,UnicodeError) as e:raise PermissionDenied('Approved document is unavailable') from e
        finally:os.close(fd)
