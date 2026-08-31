# Sebastian security and privacy model

## Guarantees by design

- `~/Library/Messages/chat.db` is opened with SQLite `mode=ro`, query-only mode,
  and short-lived connections. Sebastian never updates, copies, checkpoints, or
  otherwise modifies Messages data.
- System, untagged, attachment-only, stale, and Sebastian-signed messages are
  rejected locally before any OpenAI or Codex call. Chris's outgoing tagged
  messages are accepted as explicit invocations; Sebastian-signed outgoing
  replies cannot loop.
- Only a newly tagged conversation may leave the Mac. The API input contains the
  trigger, available sender and participant identities, at most the configured
  number of prior messages from that same conversation (never more than 20), and
  up to the configured number of supported images attached to those messages.
- Image paths must resolve inside `~/Library/Messages/Attachments`. Sebastian
  skips videos, stickers, macOS sensitive-content flags, missing files,
  unsupported formats, and files over the configured per-image or total limits.
  HEIC conversion uses a private temporary directory that is removed before the
  API call returns; encoded image data is held only in process memory.
- Conversation history is explicitly delimited as untrusted data. It cannot change
  Sebastian's system instructions or grant tools.
- Ordinary tagged conversation exposes only OpenAI's hosted web search. Local
  actions require the explicit outgoing `@sebastian codex:` command. Codex runs
  ephemerally with a workspace-write sandbox rooted at the configured workspace.
- Agent commands are owner-only (`is_from_me=1`), must be no more than two
  minutes old, and are deduplicated with a SHA-256 hash of the Messages GUID.
  Interrupted agent jobs are never automatically replayed.
- Consequential requests are paused before execution and require a random,
  six-character approval code sent from the same conversation within ten
  minutes. The Codex worker is also instructed to stop and request approval if a
  consequential step emerges during execution.
- The OpenAI key is read from the login Keychain into process memory. It is never
  accepted from config, hardcoded, printed, or logged. Use a dedicated OpenAI
  project/key so usage and revocation are isolated.
- The Codex subprocess uses the user's existing Codex login. Sebastian removes
  its dedicated API key from the subprocess environment before Codex or any
  plugin starts.
- State stores only Messages row IDs, a one-way message-GUID hash, local chat
  IDs, statuses, counters, and timestamps. A reply hash exists only while a send
  is being reconciled and is cleared after confirmation. Expired counters are
  pruned and completed trigger
  metadata is retained for seven days by default. There is no message or reply
  text column. State and config are mode 0600 in a mode 0700 directory.
- Rotating logs contain event names, local numeric IDs, status codes, counts, and
  exception class names. They do not contain bodies, reply text, handles, chat
  GUIDs, API keys, prompts, or raw provider errors.
- The kill switch is checked before monitoring and before each API or Messages
  side effect. `sebastian stop` writes it before unloading the LaunchAgent.

## Data sent to OpenAI

Only a tagged conversation's bounded text and supported image context is
submitted through the Responses API with `store=false`. Images are sent as
Base64 data URLs and count toward API usage. OpenAI service-level retention and
abuse-monitoring terms still apply independently of the API `store` flag. If the
model chooses hosted web search, a search query derived from the request may be
sent to the search service and returned sources may appear as links in the reply.

The service does not retain API or Codex input or generated replies after processing.
For crash-safe duplicate prevention it temporarily retains a one-way SHA-256
hash of the normalized outgoing reply until Messages confirms delivery or the
send is safely requeued.

## macOS permissions

Ordinary Sebastian replies need two macOS privacy grants:

- **Full Disk Access** for `~/Applications/Sebastian.app`, solely to read the
  user's Messages database.
- **Automation > Messages** for Sebastian, solely to send a reply to the same
  Messages chat ID and to verify that an account is enabled.

It does not need a separate Apple ID. Codex plugins that control browsers or the
desktop may require their own existing application permissions. Keep those
permissions attached to the Codex/ChatGPT application identities rather than
granting broad permissions to Terminal or a general Python installation.

## Threats and residual risks

- Chris can spend API quota by sending the explicit tag. Anyone in an allowed
  conversation can also spend quota by using it. Per-conversation and daily
  limits constrain this; the optional allowlist narrows incoming triggers.
- Participants may include malicious instructions in conversation text or
  images. Sebastian marks both as untrusted, supplies them only as data, exposes
  no local action tools, and keeps conversations isolated. Model prompt-injection
  risk is reduced but cannot be eliminated completely.
- Approval detection combines conservative request matching with Codex
  instructions; it is not a complete semantic proof that every possible side
  effect will be recognized. Keep the configured workspace bounded, review
  installed plugins, and use `sebastian stop` as the immediate kill switch.
- An attacker who can send messages as Chris through the same Apple account or
  control the unlocked Mac can issue owner-authorized agent commands. The
  Messages channel is convenient remote control, not a second authentication
  factor.
- Anyone with access to Chris's unlocked macOS account may inspect local state,
  change config, control the service, or access the login Keychain according to
  macOS policy. Sebastian is not a boundary against a compromised user session.
- Messages' database schema and AppleScript dictionary are undocumented platform
  interfaces and may change in a future macOS update. `sebastian doctor` fails
  closed when required tables disappear.
- Messages AppleScript provides no idempotency key or outgoing GUID. Sebastian
  records `sending` before the AppleEvent and reconciles the reply hash against
  later outgoing rows before retrying. This sharply reduces duplicates, but an
  abrupt crash at the acceptance boundary cannot be proven exactly once.
- A locally ad-hoc-signed app may need its privacy permissions approved again
  after reinstalling or rebuilding the launcher.

## Incident response

1. Run `sebastian stop` to write the kill switch and unload the agent.
2. Revoke or rotate the dedicated OpenAI project key in the OpenAI platform.
3. Disable Sebastian under Full Disk Access and Automation in System Settings.
4. Inspect `sebastian logs`; they should contain metadata only.
5. Run `./scripts/uninstall_sebastian.sh` to remove Sebastian's files and Keychain
   item without touching Messages history.

Report a suspected privacy leak only with redacted metadata. Do not attach
Messages databases, logs from unrelated tools, API keys, or conversation text.
