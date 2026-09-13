# Identity and execution boundaries

S3 defines the trusted channel contract and orchestration. Provider identity discovery and actual receivers are S4; no live adapter is connected yet. The only reasoning runtime is the V2-verified local Codex App Server.

Adapters register an exact channel/account binding and hold an in-process signing key. Events carry provider account, sender, conversation/thread, stable ID, aware timestamp, text, attachment references, and bounded history. The registry verifies signatures and issues sealed receipts; policy rechecks both. Neither credentials nor registration are model tools. HMAC seals protect the internal adapter boundary, not a substitute for provider authentication.

Owner Slack identity uses exact workspace and user IDs. Messages requires an exact configured account/owner identity plus is_from_me and verified owner account metadata. Text, names, prior owner messages, and attachments never authenticate a sender. Unknown senders receive conversation-only access. Named Slack teammates can receive curated read-only documents; absent reviewed scope fails closed.

Chris explicitly requires owner requests to receive answers in the same group chat, without a private continuation. Set `PolicyConfig.owner_group_replies=True` for his installation. This trusted configuration authorizes the originating group audience for authenticated owner requests and retains owner tools. Other participants remain restricted; text cannot set this preference. Session fingerprints include this authorization. Without configured authorization, the safe default is private continuation; `share-here` alone permits only conversation-context output. The configured owner preference supersedes that default. Routing is determined by the host, never model output. Sessions isolate channel, account, conversation, thread, sender, permissions, policy configuration, reply audience, runtime configuration, and document root/mapping. Policy changes cannot reuse an elevated session.

History is filtered to the exact account/conversation/thread, excludes the trigger and future entries, deduplicates, and contains at most 10 preceding messages with a character budget. Original unfiltered history is removed. Context is explicitly untrusted. Personal memory is never represented as channel history.

Restricted runtime threads explicitly receive no execution environments or selected capability roots. Config overrides disable each configured MCP server/plugin/app, hooks, web search, personal memories, and project instructions. An inherited custom private model instruction configuration is refused. Before every restricted turn, unexpected MCP tools or an incomplete inventory fail closed. Tool requests are checked by exact thread and turn; ordinary guests have no host tool authority. Named teammates receive only `toolbelt_read` with an enumerated document ID, no arbitrary path. The host refuses path traversal, symlinks, nonregular/multilink files, oversized files, non-text and credential-like contents. Reviewed scope configuration is a trusted administrative input.

Owner requests use existing native tools and host permissions. Requests for runtime approvals or extra credentials are denied with owner-attention errors, never fabricated. Owner tool access is not a promise that every host permission is granted. Native runtime transcripts remain separate from Sebastian's metadata; retention/reliability work belongs to S5/S6.

## Reproduction

`python3 -m unittest discover -s tests -v` runs 28 identity, impersonation, mixed-history, cross-workspace, audience, permission-change, read-scope and runtime protocol tests. Synthetic live client probe: `python3 scripts/probe_s3_service.py`. It verifies restricted canary denial, session continuity, and an actual curated document lookup. Its private output contains synthetic fixtures only in `.private/s3-service/result.json`. `scripts/probe_confinement.py` additionally records actual item types and tool inventory in `.private/confinement`.

No service is installed or live delivery enabled at S3. This candidate requires fresh independent V3 verification before channel implementation.
