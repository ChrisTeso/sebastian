# Sebastian

Chris Teso's local personal agent for the existing Sebastian Slack app and Apple Messages on this Mac. Rebuilt from scratch, using one persistent native Codex App Server for reasoning and authorized tools. Git history is preserved. OpenClaw and the recovery archive are absent from the execution path.

S1–S7 independently passed; subsequent scoped changes have their own verification records. Current installation and live delivery evidence are recorded in [progress](docs/progress.md) and [verification reports](docs/evidence/).

## Operation

Service installation, status, start and stop commands are documented in [service operation](docs/service.md). Installation is private to this Mac. Credentials and local state live under ignored `.private`; native Codex retains separate conversation transcripts. The launcher must set umask077 before starting the runtime.

Owner Slack DMs trigger automatically. Other visible Slack conversations trigger on `@sebastian`, the native bot mention, or a reply in a thread where Sebastian has already posted. Slack group DMs are unavailable with the current scopes. Messages recognizes case-insensitive `@sebastian`; verified owner self-chats also trigger automatically. Native Messages replies to a confirmed Sebastian post also trigger without a mention. Ordinary messages Chris sends to other people require a mention. Plain SMS without native reply metadata still needs a mention. Replies stay in the originating chat/thread, including owner group requests. Other senders have conversation-only access unless Chris configures a named, curated Toolbelt read grant.

## Architecture and verification

See [architecture and permissions](docs/architecture.md), [channel setup](docs/channels.md), [runtime decision](docs/runtime-decision.md), [reliability](docs/reliability.md), [privacy review](docs/s6-privacy.md), [response measurements](docs/performance.md), and [recovery evidence](docs/recovery-tests.md).

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/check_privacy.py
.venv/bin/python scripts/probe_channels.py --socket
```

The channel probe is read-only and closes its connection. No probe above sends messages. Native synthetic checks are separately documented; physical sleep testing requires owner coordination. Independent warm no-tool sample:20 requests, median3.733s and p954.991s, with fixture transport and actual native reasoning. Live delivery is a separate gate.

The previous26 top-level repository entries were removed after an independently verified recovery archive was created at `/Users/teso/.codex/recovery/sebastian-20260912T162029Z`. That private directory contains `repository.tar.gz`, hashes, original service plists and rollback instructions. Restore only as an explicit rollback with the new service stopped; never run old and new consumers together. The local Git history remains preserved. Public releases use a current-code snapshot based on the existing public repository history; private local ancestry is not published.
