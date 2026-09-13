# V4 independent verification — FAIL

Verifier: `/root/v4`, fresh context. Date: 2026-09-12. Scope: S4 only, approved plan including same-chat owner authorization. No frozen source edits. S5 is not unlocked.

## Frozen artifact checks

All 13 files in `docs/evidence/s4-artifact-hashes.json` matched their exact SHA-256 values before and after the independent checks. Manifest SHA-256: `dd70f05e2d1dca290d4d86ae3e82cab0f3d2830eee667236e0aeb06b9a3ff52d`. All five S2 and 14 S3 manifest entries also matched. `git fsck --full` exited 0 with dangling objects only, no corruption.

## Blocking findings

1. **A single malformed Messages body indefinitely blocks later valid requests.** `sebastian/messages.py:169` decodes each row before progressing the cursor; any unsupported attributedBody aborts the entire poll. The cursor is updated only at line 181. An independent synthetic fixture inserted a malformed archive at row 1 and a valid owner mention at row 2: two successive polls both raised ValueError and highwater remained zero. The later valid event was never returned. This also occurs when the malformed row is a reaction that ingress correctly skips: `history()` at line 148 decodes it again while constructing the subsequent valid event. Repair must isolate malformed/unsupported records, preserve a sanitized failure indication, and allow valid later input/history to proceed without treating failed content as executable instructions. Add regression coverage for ingress and history; documenting retry behavior or deferring it to S5 does not repair this adapter-level poison-record loop.

2. **Current installation metadata does not recognize owner SMS/RCS events.** A read-only metadata query of the ten latest outgoing records per service found all ten iMessage records matching the configured account-pair/chat-login conjunction, but zero of ten SMS and zero of ten RCS records matching. The account/login evidence alone must not silently replace trusted account-guid verification; verify the relay account metadata through trusted local account setup, then configure the supported pairs and test owner authentication across these synchronized transports. As currently configured, those owner mentions become `unverified-local` at lines 128–129 and lose owner permissions. This contradicts the plan's synchronized Messages coverage, even though the transport correctly fails identity closed. No message bodies were read for this finding.

## Passing evidence

- `.venv/bin/python -m unittest discover -s tests -v`: 56 tests passed. Existing fixtures cover direct/group/thread routing, textual mixed-case and native mentions, sender/account checks, attachment references, self/duplicate suppression, startup cutoff/highwater, bounded history, injected send transport arguments, and same-chat owner policy.
- `.venv/bin/python scripts/probe_channels.py --socket`: live bot/app identity and existing owner DM checks passed, exact scope metadata was returned, Socket Mode handshake connected successfully and the client closed. No receiver, model or send was attached. Observed scopes include public/private channel history/read, IM history/read, app mentions and chat write; `mpim:read` and `mpim:history` remain absent, matching the documented group-DM limit.
- Separate live `users.info` check confirmed configured owner exact user/team IDs, nonbot status and active account. No secret values or profile contents were printed.
- Live Messages connection reported `query_only=1`, two configured self-chats and first-start cursor equal to current maximum, without polling historical bodies. Independently checked both self-chats have direct style, exactly one participant, a configured account login, and a participant matching the verified login alias after removing its E:/P: prefix and normalizing case. Existing configured account pairs occur on outgoing records.
- Private installation config is mode 0600 and requires `owner_group_replies=True`. Existing self-chat allowlist limits automatic owner triggers; other outgoing direct chats require a mention. Plugin guidance is separate from the event receiver, contains no credentials and grants no authority.
- Source review found fixed-source Messages AppleScript with exact chat/body argv and sanitized sender errors; Slack send uses verified bot client and exact destination/thread without impersonating Chris. These are fixture/source checks, not live delivery proof.
- Current official [Slack conversations.replies documentation](https://docs.slack.dev/reference/methods/conversations.replies/) lists bot-token channel/group history scopes; no obsolete user-token-only restriction was inferred. Actual event subscriptions and live end-to-end channel delivery remain later provider/live gates.

Private reproducible evidence: `.private/v4/verify.py` and `.private/v4/results.json`, owner-only. The supplemental checks intentionally reproduce failures without altering the frozen implementation. No raw private conversation, attachment contents, credentials, continuous service startup, live send, old source/archive read, push, merge or publication occurred.

Repair both findings, update the frozen candidate and obtain a new independent V4 PASS before S5. Durable queue/ledger integration, real delivery and live inbound evidence remain downstream requirements.
