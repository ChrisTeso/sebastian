# Installed service and live proof

The new `com.christeso.sebastian.native` per-user LaunchAgent is installed and enabled. It launches the freshly compiled/signed `/Users/teso/Applications/Sebastian Native.app`, whose child runs this checkout's new Python daemon. `status` independently reports registration, a held instance lock, fresh heartbeat and connected Slack/readable Messages. The dedicated app has Full Disk Access; the general Python interpreter remains ungranted. Actual Messages final delivery also succeeded from the service. Legacy Sebastian/OpenClaw labels remain disabled and unloaded. No legacy app/source was reused or deleted.

## Controlled live tests

These are **automated owner-account tests**, not manually authored user messages. The existing authenticated owner Slack connector sent requests only into Chris's existing Sebastian DM. A local Messages send targeted only an already verified owner self-chat. No third-party participant received a test. Provider events entered the running service's real listeners, used the selected native runtime, and returned through the real final transports. Negative permission/group scenarios remain fixture evidence, not live third-party tests.

Read-only `scripts/check_live.py` verifies exact provider receipt records, original destination, sender identity and synthetic expected final. Private evidence `.private/s7-live/results.json` has all three tests complete with one confirmed final each:

- Owner Slack DM: `SLACK-LIVE-739126`, received from the configured owner and answered by the existing Sebastian bot in the same DM.
- Owner Messages mention retest: `MESSAGES-LIVE-739127`, one accepted request and one final reply in the same verified self-chat.
- Owner Chrome request: `CHROME-LIVE-739126 Example Domain`. Exact native session tool records show Chrome opening a new temporary example.com tab, returning the Example Domain title/page, and closing that same tab. The final returned through Slack. No user tab was closed.

New deployed native transcripts are mode0600; the queue/status/session references are private metadata. The observed first Slack request took5.524 seconds in the engine, including0.772 seconds provider retrieval,4.456 seconds runtime/model, and0.293 seconds final delivery. The Chrome request took13.012 seconds, including11.934 seconds native runtime/tool time. These are individual live observations, not percentile claims. The independently verified20-request warm no-tool benchmark is separately documented.

## Self-chat mirror finding and repair

The first Messages test (`MESSAGES-LIVE-739126`) exposed a real mirror defect: macOS stored separate outgoing/incoming copies, causing two jobs and two replies. The first V7 correctly failed; that history is retained in `docs/evidence/v7-first-fail.md`. The live checker was repaired to count matching request jobs across both copies instead of checking only the canonical owner event. It now independently detects the historical duplicate.

The narrow ingress repair ignores only incoming copies in a configured private self-chat whose sender matches frozen owner aliases. Outgoing owner authentication and genuine other/group requests remain unchanged. Fresh S4 revalidation passed in `docs/evidence/v4-mirror.md` (119 full tests). The new live retest's two database records are both present, the durable cursor is past both, the outgoing record has exactly one job, and the incoming mirror has zero jobs. `.private/s7-live/mirror-retest.json` records these facts. The old completed jobs were not erased or replayed.

## Lifecycle proof

`.private/s7-live/stop-evidence.json` records the actual service stop: registration unloaded, lock released, app/daemon/native process all exited. After restarting with the repaired source, a second lifecycle check sent SIGTERM only to the verified idle daemon. launchd automatically brought up a new healthy daemon within8.359 seconds; both Slack and Messages were healthy, and the old daemon/native runtime exited. Evidence: `.private/s7-live/restart-evidence.json`. This is an observed automatic restart, distinct from plist configuration alone. The replacement runtime starts lazily on the next request.

The original seven-stage plan, S4 repair revalidation, and final V7 report remain separate evidence gates. Consult `docs/progress.md` for the final gate decision. No push, merge or publication is required or performed.
