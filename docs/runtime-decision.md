# S2 runtime decision — awaiting V2

Candidate: persistent local Codex App Server using the installed desktop-bundled executable. This is the only proposed reasoning runtime. No replacement service has been implemented or installed yet. S3 requires independent V2 PASS.

## Evidence

The Agents API was tested with a fresh self-hosted Mac environment and a separate restricted executor key. The existing application key remained in the application process; only the executor key entered the executor environment. The API accepted the session and reported the Mac environment connected.

| Required capability | Agents API | Local App Server |
| --- | --- | --- |
| Read harmless local fixture | Observed exact marker | Observed exact marker |
| Continue same conversation | Observed remembered marker | Observed remembered marker |
| Inspect existing Chrome session | Native cua_repl tab inventory | Native cua_repl tab inventory |
| Control harmless Mac app | Calculator display 56 | Calculator display 56 |
| Authenticated service-plugin read | Existing GitHub plugin get_profile unavailable | github.get_profile completed, no tool error |
| Cancellation and reconnection | Cleanup cancellation acknowledged; executor exited 0; no full recovery probe after capability failure | Interrupted an active command, then completed follow-up; fresh process resumed same thread and remembered marker |

Agents API received both installed plugin roots in `environment.capability_directories`. The GitHub package uses `.app.json` to identify a connected service; its existing managed authentication was not made available in that API session. No token extraction, second runtime, CLI substitute, or custom authentication bridge was used to change that result. Generic separately authenticated MCP support in Agents API does not prove compatibility with the required existing connection. Desktop control itself worked; it must not be reported as unavailable.

The preliminary Chrome probes prove inventory access to the existing browser, but do not separately verify a signed-in website's account controls. V2 must include that distinction and verify the existing logged-in session through non-message account UI. The original evidence remains in `.private/probes/` and must be inspected at the underlying tool-result level, not accepted solely from model final messages.

## Versions and dependencies

- Tested Mac executable: `/Applications/ChatGPT.app/Contents/Resources/codex`, `codex-cli 0.154.0-alpha.6.2`.
- Executable SHA-256: `ecad78dbf98adb89ec475edac86630406cbe59d9f3070b17d88065f136b94bcb`.
- PATH CLI version: 0.151.0, not the tested App Server binary.
- Desktop plugin: `unified-computer-use`, installed version `26.908.40834`, with its desktop-bundled Node/CUA executable and existing OS/browser permission mechanisms.
- GitHub installed plugin: `0.1.12-5f7cd798dc99`; App Server exposes its authenticated tool through `codex_apps`.
- App Server uses native managed ChatGPT sign-in; account/read reported Pro. No access/refresh tokens were copied, extracted, or injected by these probes. The process receives only HOME, PATH, TMPDIR, USER, LOGNAME, LANG and SHELL from the parent environment. Calling-task pipe, originator and session variables are excluded.
- The desktop app, local helper services, Chrome extension, existing logged-in Chrome profile, and OS permissions are required host dependencies. Running on this Mac is not a guarantee of availability while asleep, signed out, disconnected, or after a desktop/plugin update.
- App Server stdio is used by the probes. The official interface is experimental; version updates require compatibility checks. No unauthenticated network App Server listener is exposed.

## Billing and retention

Agents API model usage is billed at API model rates; self-hosted compute runs on this Mac. The trial used gpt-6-astra. No credits or subscriptions were purchased. The disposable API sessions were deleted and their executors stopped. The restricted executor key Chris authorized remains in an ignored mode-0600 file for controlled reproduction; it is not an active service dependency.

App Server with managed ChatGPT authentication draws from the existing account's shared Codex/ChatGPT Work allowance and applicable credit limits. It is not assumed free or unlimited, and no exact per-message cost is inferred from these probes. The account API's Pro label does not establish the subscription tier's invoice amount.

The App Server stores durable conversation/session history in the user's normal Codex store. Synthetic probe threads contain fixtures and tool outputs. Their full raw outputs stay in private evidence, excluded from Git; production retention controls must be documented and enforced in S5/S6. A metadata-only Sebastian ledger will not eliminate the runtime's separate session retention.

## Reproduction and artifacts

- `python3 scripts/probe_app_server.py`: isolated synthetic file and continuity probes, minimal process environment.
- `python3 scripts/probe_app_server_capabilities.py`: resumes its thread; native service/Chrome/Calculator calls; active-turn interruption and follow-up.
- `python3 scripts/probe_agents_api.py`: uses the existing application Keychain item plus the separately authorized restricted executor key; creates and cleans up one disposable self-hosted session. Generic HTTP 400 for unsupported inline `agent.timeout` and empty-body event acknowledgements were corrected during harness setup before the reported capability run.
- `scripts/store_executor_key.py`: one-use loopback form used to store the authorized restricted key without exposing it in tool output; server exited after storage and browser tabs closed.
- `.private/probes/agents-summary.json`, `agents-events.jsonl`, `agents-*-items.json`: private API evidence.
- `.private/probes/app-server-initial.json`, `app-server-capabilities.json`: private local runtime evidence.
- `docs/evidence/s2-artifact-hashes.json`: immutable candidate implementation hashes, excluding private keys and mutable progress.

These are S2 test harnesses, not production adapters or delivery code. Never run tests that send to other people. A fresh verifier must reproduce capabilities using independent fixtures and separate evidence files, and return PASS, FAIL, or BLOCKED.

## Official sources inspected

- [Agents API overview and billing](https://developers.openai.com/api/docs/guides/agents-api/overview).
- [Self-hosted executor and authentication](https://developers.openai.com/api/docs/guides/agents-api/environments/self-hosted).
- [Agents API plugin loading and authentication](https://developers.openai.com/api/docs/guides/agents-api/tools/plugins).
- [Session lifecycle](https://developers.openai.com/api/docs/guides/agents-api/sessions) and [event/recovery semantics](https://developers.openai.com/api/docs/guides/agents-api/sessions/events).
- [Codex App Server protocol, native authentication, and limits](https://learn.chatgpt.com/docs/app-server).
- [Desktop plugin scope and authentication limitations](https://learn.chatgpt.com/docs/plugins).
- [Computer Use host and Chrome requirements](https://learn.chatgpt.com/use-cases/use-your-computer-with-codex).
- [Codex billing basis](https://learn.chatgpt.com/docs/pricing).
