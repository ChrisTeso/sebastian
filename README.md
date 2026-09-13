# Sebastian

Sebastian is Chris Teso's personal AI assistant for Slack and Apple Messages. It runs locally on macOS, answers questions, uses authorized personal tools, and generates and sends images.

The current implementation is on [`main`](https://github.com/ChrisTeso/sebastian/tree/main).

## What it does

- Responds to mentions and follow-up replies in Slack and Messages.
- Keeps replies in the originating chat or Slack thread under the configured audience policy.
- Reads relevant conversation context and supported image attachments.
- Generates and delivers images through Slack and Messages.
- Uses a lightweight model for simple replies and delegates harder requests to more capable models.
- Identifies the responding model and signs messages as **– Sebastian, Chris's AI Assistant**.

## Starting a conversation

| Channel | What activates Sebastian |
|---|---|
| Slack | A direct message from Chris, an `@Sebastian` mention, or a reply in a thread where Sebastian has already posted. |
| Apple Messages | An `@sebastian` mention, a native reply to a confirmed Sebastian message, or a message in Chris's configured private self-chat. |

Messages replies come from Chris's existing account. Sebastian does not have a separate phone number or Apple Account. Plain SMS without native reply metadata still requires a mention outside the configured self-chat. Slack group DMs are unavailable with the current installation's scopes.

Each request retrieves up to ten previous eligible messages, capped at 16,000 characters. Slack context stays within the relevant thread or preceding top-level channel messages; Messages context stays within the same chat. Persistent model sessions may retain earlier interactions beyond that freshly retrieved context.

## Model routing

| Model | Role |
|---|---|
| Luna (`gpt-5.6-luna`) | Answers simple self-contained requests without tools, or selects an escalation. |
| Sol (`gpt-5.6-sol`) | Handles routine research and tool requests. |
| Astra (`gpt-6-astra`) | Handles complex analysis, coding, consequential decisions, and uncertain routing. |

Self-contained owner image-generation requests can go directly to an isolated Sol runtime. Separate text and image workers let an ordinary request in another conversation proceed while an image is being generated. Requests within the same conversation and thread remain ordered.

Routing never grants permissions. The host authenticates the sender, selects the allowed tools, separates sessions, and chooses the reply destination. Other participants do not inherit Chris's personal access by mentioning Sebastian or replying to him.

## Local operation

The rebuilt service uses persistent local Codex App Server sessions and a native macOS launcher. OpenClaw is not part of the execution path. This repository documents a personal installation, not a one-command hosted service.

From the configured checkout and virtual environment:

```sh
.venv/bin/python scripts/service.py status
.venv/bin/python scripts/service.py start
.venv/bin/python scripts/service.py stop
```

The Mac must be logged in, awake, and online. Initial setup requires private installation configuration, Slack authorization, and the appropriate macOS permissions. See [service setup and operation](https://github.com/ChrisTeso/sebastian/blob/main/docs/service.md).

## Privacy and reliability

- The Apple Messages database is opened read-only.
- Credentials, installation configuration, and runtime state are excluded from Git under `.private`.
- Sebastian's ledger stores message references, delivery receipts, and fingerprints rather than raw conversation bodies or images.
- Native Codex conversation transcripts are retained separately; local execution does not mean zero retention or offline inference.
- Duplicate and self-echo detection prevent replies from triggering themselves. Interrupted actions are not automatically replayed.

## Verification

The current rebuild passes **174 automated tests** and has independent review records. Live owner-only tests have verified Slack and Messages text/image delivery and model routing. The newer reply-without-mention behavior has automated and independent verification; live inbound confirmation remains pending.

```sh
.venv/bin/python -m unittest discover -s tests
```

Installation-specific read-only checks, which require private local configuration:

```sh
.venv/bin/python scripts/check_privacy.py
.venv/bin/python scripts/probe_channels.py
```

See [verification history](https://github.com/ChrisTeso/sebastian/blob/main/docs/progress.md), [review evidence](https://github.com/ChrisTeso/sebastian/tree/main/docs/evidence), [model routing](https://github.com/ChrisTeso/sebastian/blob/main/docs/model-routing.md), and [permission boundaries](https://github.com/ChrisTeso/sebastian/blob/main/docs/permissions.md).
