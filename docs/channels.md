# Channel setup and S4 evidence

The new adapters use the existing Sebastian Slack app and this Mac's Messages account. They do not load any legacy implementation. The native Slack connector sends as Chris, so final Slack delivery uses the existing bot token through Slack's supported SDK. The native Messages plugin provides read/send tools but does not expose the account, is_from_me, attributedBody and trigger contract required by ingress. The small Messages listener opens chat.db read-only; exact-chat AppleScript delivery fills the host delivery gap. Native plugin tools remain available to authorized owner reasoning.

`plugins/sebastian-channels` packages channel guidance separately from the persistent receiver. It holds no credentials, grants no authority and runs no listeners. Its manifest and skill validate. No marketplace entry or plugin installation has been made in S4.

## Verified configuration

Private installation configuration is `.private/config/installation.json`, mode0600 under mode0700 directories. It references two mode0600 Slack credential files. Existing Slack app/bot credentials were copied once from their active local secret store and verified against Slack's supported API; the resulting service has no dependency on that store or OpenClaw. No native OAuth/session credential extraction was performed. Actual credential values never enter ordinary files or output.

Slack auth.test, bots.info and users.info verify the existing Sebastian app and exact owner workspace/user. The existing owner DM was located and verified without creating one. The native authenticated Slack connector independently identified the owner account. Live identities and scopes are recorded privately in `.private/config/slack-identity.json`. Granted scopes support visible public/private channels and one-to-one DMs; mpim:read/history are absent, so group DMs remain unavailable rather than claimed supported. No mass invitations or scope/app changes were made.

Messages owner metadata is derived from the exact intersection of ActiveAccounts and OnlineAccounts in the local iMessage preferences, matching account_guid on is_from_me database rows, account identifiers and chat account_login. Current SMS/RCS pairs additionally use the latest outgoing is_from_me record for each service, requiring both its account and chat login to match the previously verified own aliases; these pairs are frozen at setup and never learned from runtime input. The SMS GUID also matches a native Messages service ID. The RCS GUID is not in that native service list; its provenance is the read-only outgoing account/alias correlation. Older migrated account GUIDs remain untrusted. Verified self-chat GUIDs have exactly one participant matching a verified account login alias. There are two existing verified self-chats. No names or text claims were used. Ordinary owner texts to other people do not trigger: only allowlisted self-chats auto-trigger; other direct/group conversations require @sebastian. No raw conversation bodies were read or saved for setup.

Chris's explicit same-chat preference is persisted as owner_group_replies=true. Owner group requests retain personal tools and the original group/thread destination. Every other sender remains subject to S3 permissions. No DM continuation is required for Chris.

## Checks

- `.venv/bin/python -m unittest discover -s tests -v`: 61 PASS (28 prior boundaries, 15 Slack, 16 Messages, two installation/private-storage checks).
- `.venv/bin/python scripts/probe_channels.py --socket`: exact Slack bot identity and owner DM PASS; supported Socket Mode connection handshake PASS; connection closed. No receiver, model call or reply send attached.
- Same probe: Messages query_only=1, two verified self-chats, first-start cursor at current maximum and latest iMessage/SMS/RCS owner metadata PASS; connection closed without polling historical bodies.
- Plugin manifest and skill validators PASS. S2/S3 artifact hashes unchanged.

Fixtures cover owner/nonowner DM, group/thread/mention routing, native mentions, wrong workspace/app/user, attachments, duplicate/self events, system/reaction filters, backlog exclusion, exact sender destination and attributedBody decoding including Unicode. Real Messages sends, final Slack bot delivery and live end-to-end events remain S7. SDK/AppleScript success semantics must not be mistaken for exactly-once delivery; durable reconciliation is S5. Fresh V4 is required before S5.

Sources: [Slack auth.test](https://docs.slack.dev/reference/methods/auth.test/), [users.info](https://docs.slack.dev/reference/methods/users.info/); adapter-specific sources and APIs in slack.md and messages.md.
