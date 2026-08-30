# Sebastian security and privacy model

## Guarantees by design

- `~/Library/Messages/chat.db` is opened with SQLite `mode=ro`, query-only mode,
  and short-lived connections. Sebastian never updates, copies, checkpoints, or
  otherwise modifies Messages data.
- System, untagged, attachment-only, and Sebastian-signed messages are rejected
  locally before any OpenAI call. Chris's outgoing tagged messages are accepted
  as explicit invocations; Sebastian-signed outgoing replies cannot loop.
- Only a newly tagged conversation may leave the Mac. The API input contains the
  trigger, available sender and participant identities, and at most the configured
  number of prior messages from that same conversation (never more than 20).
- Conversation history is explicitly delimited as untrusted data. It cannot change
  Sebastian's system instructions or grant tools.
- The only model tool is OpenAI's hosted web search. Message requests cannot run
  shell commands, access files, send email, use calendars, make purchases, or take
  financial or account actions.
- The OpenAI key is read from the login Keychain into process memory. It is never
  accepted from config, hardcoded, printed, or logged. Use a dedicated OpenAI
  project/key so usage and revocation are isolated.
- State stores only Messages row IDs, local chat IDs, statuses, counters, and
  timestamps. A reply hash exists only while a send is being reconciled and is
  cleared after confirmation. Expired counters are pruned and completed trigger
  metadata is retained for seven days by default. There is no message or reply
  text column. State and config are mode 0600 in a mode 0700 directory.
- Rotating logs contain event names, local numeric IDs, status codes, counts, and
  exception class names. They do not contain bodies, reply text, handles, chat
  GUIDs, API keys, prompts, or raw provider errors.
- The kill switch is checked before monitoring and before each API or Messages
  side effect. `sebastian stop` writes it before unloading the LaunchAgent.

## Data sent to OpenAI

Only a tagged conversation's bounded context is submitted through the Responses
API with `store=false`. OpenAI service-level retention and abuse-monitoring terms
still apply independently of the API `store` flag. If the model chooses hosted
web search, a search query derived from the request may be sent to the search
service and returned sources may appear as links in the reply.

The service does not retain the API input or generated reply after processing.
For crash-safe duplicate prevention it temporarily retains a one-way SHA-256
hash of the normalized outgoing reply until Messages confirms delivery or the
send is safely requeued.

## macOS permissions

Sebastian needs two macOS privacy grants and no others:

- **Full Disk Access** for `~/Applications/Sebastian.app`, solely to read the
  user's Messages database.
- **Automation > Messages** for Sebastian, solely to send a reply to the same
  Messages chat ID and to verify that an account is enabled.

It does not need Accessibility, Contacts, Screen Recording, Location, or a
separate Apple ID. Do not grant these permissions to Terminal or a broad Python
installation merely to make Sebastian work; grant them to the installed app
identity.

## Threats and residual risks

- Chris can spend API quota by sending the explicit tag. Anyone in an allowed
  conversation can also spend quota by using it. Per-conversation and daily
  limits constrain this; the optional allowlist narrows incoming triggers.
- Participants may include malicious instructions in conversation history.
  Sebastian marks history as untrusted, supplies it only as data, exposes no local
  action tools, and keeps conversations isolated. Model prompt-injection risk is
  reduced but cannot be eliminated completely.
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
