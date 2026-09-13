"""Owner-only installation configuration. No embedded provider credentials."""
from dataclasses import dataclass,field
from pathlib import Path
import json,os,stat
from .contracts import Channel,Destination
from .policy import PolicyConfig
from .messages import OwnerMetadata


def private_read(path:Path)->str:
    path=Path(path)
    if not path.is_absolute():raise ValueError('Private file path must be absolute')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077 or info.st_nlink!=1 or info.st_size>100_000:
            raise PermissionError('Private file ownership or mode is unsafe')
        return os.read(fd,100_001).decode('utf-8')
    finally:os.close(fd)

@dataclass(frozen=True)
class Settings:
    policy:PolicyConfig
    slack:dict=field(repr=False)
    messages:OwnerMetadata=field(repr=False)
    messages_db:Path=field(repr=False)
    owner_workspace:Path
    restricted_workspace:Path
    state_dir:Path

    def slack_credentials(self)->tuple[str,str]:
        bot=private_read(Path(self.slack['bot_token_file'])).strip()
        app=private_read(Path(self.slack['app_token_file'])).strip()
        if not bot.startswith('xoxb-') or not app.startswith('xapp-'):raise ValueError('Unsupported Slack credentials')
        return bot,app


def load_settings(path:Path)->Settings:
    raw=json.loads(private_read(path))
    if raw.get('version')!=1 or raw.get('owner_group_replies') is not True:
        raise ValueError('Chris installation requires same-chat owner group replies')
    slack=raw['slack'];m=raw['messages']
    owner=OwnerMetadata(m['account_id'],m['sender_id'],frozenset(tuple(p) for p in m['account_pairs']),frozenset(m['chat_logins']),frozenset(m['owner_private_chat_ids']))
    if not owner.owner_private_chat_ids:raise ValueError('A verified owner self-chat is required')
    policy=PolicyConfig(owner_slack=frozenset({(slack['team_id'],slack['owner_user_id'])}),owner_messages=frozenset({(owner.account_id,owner.sender_id)}),owner_group_replies=True,owner_destinations=(Destination(Channel.SLACK,slack['team_id'],slack['owner_dm']),Destination(Channel.MESSAGES,owner.account_id,sorted(owner.owner_private_chat_ids)[0])),permission_revision=raw.get('permission_revision','1'))
    paths=[Path(raw[k]) for k in ['messages_db','owner_workspace','restricted_workspace','state_dir']]
    if any(not p.is_absolute() for p in paths):raise ValueError('Absolute installation paths required')
    return Settings(policy,slack,owner,*paths)
