# Sebastian

Sebastian is a local, user-level macOS service that watches Apple Messages and
replies to new sent or received messages that explicitly contain `@sebastian`
(case-insensitive). It sends from the Messages account already signed in on this
Mac; it does not need or create another Apple ID.

It scans all conversations, retains no message bodies, and has its own state,
configuration, logs, LaunchAgent, app identity, and OpenAI Keychain item.

### Host architecture

The current implementation targets macOS 26 on Apple Silicon:

- Receive: SQLite opens `~/Library/Messages/chat.db` with `mode=ro` and
  `PRAGMA query_only=ON`. It polls by increasing Messages row ID and reads the
  live WAL normally; it never writes to the Messages database.
- Decode: Python plus `pytypedstream` handles `attributedBody`, where modern
  Messages stores most text on this Mac. Same-conversation JPEG, PNG, WebP, and
  HEIC image attachments can be included for visual interpretation; HEIC is
  converted to JPEG in a private temporary directory and immediately removed.
- Generate: ordinary tagged chat uses the OpenAI Responses API, `store=false`,
  and only hosted `web_search`. Image inputs use bounded in-memory data URLs.
- Act: an outgoing, fresh `@sebastian codex:` command launches the locally
  authenticated Codex CLI in `~/Code` with a workspace-write sandbox and the
  user's installed Codex skills and plugins. Agent prompts and results stay in
  memory or private temporary files that are removed after each run.
- Send: Messages' native AppleScript `send ... to chat id`, which preserves the
  originating direct or group conversation.
- Run: `~/Applications/Sebastian.app` is the stable permission identity, started
  by a per-user LaunchAgent in the logged-in GUI session.

### Install

Before installing, create a dedicated API project and secret key in the OpenAI
platform. Then run:

```bash
./scripts/install_sebastian.sh
```

The installer builds the private runtime under
`~/Library/Application Support/Sebastian`, reuses an accessible Sebastian
Keychain item or creates one from hidden terminal input, installs
`Sebastian.app`, writes the LaunchAgent, and adds the CLI at
`~/.local/bin/sebastian`. The native app reads the key from Keychain and passes
it only to its private Python child process in memory.

The installer pauses twice for actions only you can approve:

1. In **System Settings > Privacy & Security > Full Disk Access**, add and enable
   `~/Applications/Sebastian.app`.
2. When macOS says Sebastian wants to control Messages, choose **Allow**. The
   permission probe reads account status; it does not send a message.

The installer then runs a read-only database check, a minimal OpenAI
connectivity check, and a local dry-run. It asks before the real Messages test.
For that live test, send a new message containing `@sebastian` in a direct or
group conversation; Sebastian replies in that same conversation.

If `~/.local/bin` is not already in `PATH`, add this to `~/.zshrc`:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

### Operate

```bash
sebastian status
sebastian start
sebastian stop
sebastian restart
sebastian logs
sebastian doctor
sebastian doctor --permissions
sebastian doctor --openai
sebastian doctor --codex
sebastian test --dry-run
sebastian test --live
sebastian allowlist
sebastian config
sebastian config --edit
```

`sebastian stop` creates the persistent kill-switch file before unloading the
LaunchAgent. `sebastian start` removes it. The service checks that switch before
polling, before calling OpenAI, and again before sending.

The allowlist is off by default. Your own explicit `@sebastian` messages are
always accepted. For incoming messages, a conversation GUID authorizes the whole
chat and a participant entry authorizes only tagged messages sent by that
participant, including inside a group. Enable it only after adding an exact value:

```bash
sebastian allowlist --add-chat 'iMessage;+;chat...' --enable
sebastian allowlist --add-participant '+15555550100' --enable
```

Entries are stored only in the local mode-0600 configuration and are never
printed by `sebastian allowlist` or written to logs.

### Codex over Messages

Agent commands are separate from ordinary Sebastian conversation:

```text
@sebastian codex: inspect the Mini Me repository and summarize failing tests
@sebastian status
@sebastian cancel
@sebastian approve A1B2C3
```

Only an outgoing message identified by Messages' `is_from_me` field can invoke
Codex or use an approval code. Agent commands must be no more than two minutes
old when discovered and are deduplicated using a one-way hash of the Messages
GUID, so an older iCloud insertion is not replayed. One Codex job can run at a
time. Interrupted jobs are marked terminal and are never automatically rerun.

Codex runs ephemerally, inherits the existing Codex login and enabled plugins,
and does not inherit Sebastian's OpenAI API key. Common external, destructive,
financial, account, deployment, publishing, and communication requests pause
before execution and return a six-character approval code valid for ten minutes
in that same conversation. Codex may also request approval if a consequential
step emerges during otherwise safe work.

The default writable workspace is `~/Code`. Computer and browser control require
the Mac to be awake, signed in, and able to open the relevant local application.

### Configuration

Conservative defaults live in `config.sebastian.example.json`. The installed
copy is `~/Library/Application Support/Sebastian/config.json` and supports:

- the OpenAI model and reasoning effort (`gpt-5.6-sol` with `medium` reasoning by default);
- maximum response characters (including the signature);
- maximum same-conversation context, capped at 20 messages;
- image interpretation on/off, capped at four images, 10 MB per image, and
  20 MB total by default;
- triggers per conversation per minute;
- global daily API calls;
- metadata-state retention (seven days by default; counters are pruned sooner);
- an optional exact allowlist;
- polling and duplicate-reconciliation intervals; and
- hosted web search on/off; and
- agent enablement, two-minute freshness window, Codex executable, writable
  workspace, sandbox, timeout, approval lifetime, model, and reasoning effort.

Restart Sebastian after changing configuration.

### Troubleshooting

- **Messages database permission fails:** remove and re-add
  `~/Applications/Sebastian.app` under Full Disk Access, then run
  `sebastian doctor --permissions`.
- **Automation fails or error -1743 appears:** open **System Settings > Privacy &
  Security > Automation**, enable Messages under Sebastian, then rerun the
  permission doctor.
- **Key unavailable:** rerun the installer to update the Keychain item. Never put
  the key in `.env`, config, a shell profile, or a log.
- **Background item disabled:** re-enable Sebastian in macOS Login Items &
  Extensions, then run `sebastian start`.
- **Homebrew Python changed:** rerun the installer; it recreates Sebastian's
  private virtual environment while preserving its config and state.
- **No reply:** confirm the message contains the explicit tag, is
  not over either rate limit, and is allowed when the allowlist is enabled. Use
  `sebastian status` and metadata-only `sebastian logs`.
- **Image unavailable:** Sebastian supports downloaded JPEG, PNG, WebP, and
  HEIC images from the trigger or recent same-conversation context. It skips
  videos, stickers, macOS sensitive-content flags, missing iCloud files,
  unsupported formats, and files over the configured limits.
- **Codex unavailable:** run `codex --version`, update the CLI if necessary, then
  run `sebastian doctor`. Sebastian currently requires a Codex CLI new enough to
  support the configured model and an existing local Codex login.

### Uninstall

```bash
./scripts/uninstall_sebastian.sh
```

This unloads the service and removes Sebastian's app, runtime, state, logs,
LaunchAgent, CLI, and Keychain item. It does not touch Messages, its database,
or conversation history.

### Reliability boundary

Messages' AppleScript API has no idempotency key and returns no durable outgoing
message ID. Sebastian uses a write-ahead `sending` state plus same-conversation
outgoing-message hash reconciliation before retrying. This is duplicate-resistant
across restarts and the strongest practical local design, but a power loss at the
precise AppleEvent acceptance boundary cannot be given a mathematical exactly-once
guarantee.

See [SECURITY.md](SECURITY.md) for the full privacy and threat model.
