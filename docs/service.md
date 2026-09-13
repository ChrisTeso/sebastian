# Local service control

The new service is a per-user LaunchAgent, `com.christeso.sebastian.native`. It runs `/usr/bin/open -W -n ~/Applications/Sebastian Native.app`. This newly compiled native app launches this checkout's `.venv/bin/python -m sebastian.daemon`, which hosts the verified `Engine` and lazy persistent `SessionRuntime`. It uses `.private/config/installation.json`. No legacy service, app wrapper, recovery archive, or OpenClaw is used. The checkout and virtual environment must stay at their installed absolute paths.

From `/Users/teso/Code/Mini Me`:

```sh
.venv/bin/python scripts/service.py install
.venv/bin/python scripts/service.py start
.venv/bin/python scripts/service.py status
.venv/bin/python scripts/service.py stop
```

`install` compiles `native/Launcher.swift` into the new `~/Applications/Sebastian Native.app`, applies ad-hoc signing with a stable designated bundle-identifier requirement, verifies its signature, and writes `~/Library/LaunchAgents/com.christeso.sebastian.native.plist`. `start` enables and loads that exact label. Once enabled, launchd starts it at user login and restarts it after an exit, throttled to at least ten seconds. `start` does not interrupt an already running instance. `stop` disables future login startup and unloads that label; `start` enables it again. No command clears the ledger, cursors, pending deliveries, or session IDs. After updating daemon code, use `stop` followed by `start` to run the new code.

The daemon sets umask 077 before settings/state/native runtime startup, with launchd applying the same mask at process creation. The state directory is owner-only, and `service.lock` enforces one daemon per configured state directory using a kernel lock. It remains on disk after exit; do not delete it to try to unlock a live daemon. The OS releases it when its process exits. No PID from a status file is used as authority to terminate a process.

The native app forwards termination to its Python child and stays alive until that child exits. The `open -W` lifetime gives launchd automatic restart when the app/daemon exits. Stop unloads `open` and then asks only the matching new bundle to terminate, waiting for completion. The daemon also detects loss of its launcher parent within a heartbeat interval and shuts down. SIGTERM/SIGINT set the engine stop event and request active-runtime cancellation. The engine joins its worker, closes Socket Mode, the runtime, and ledger. launchd allows 45 seconds before forced termination. A forced exit is reconciled by the existing engine on restart; interrupted actions are not silently replayed. Stop can take time when an external operation is awaiting cancellation.

`status` reports launchd registration separately from the process lock and a heartbeat written approximately every five seconds. It includes service/native runtime PID, Slack connection state, a read-only Messages-table query result, and the last measured engine stages in seconds. `last_failure` is a historical, fixed category, not a current health verdict; it contains no exception text. Timings describe the latest observed request and may still be filling while a request runs. A heartbeat under twenty seconds old is marked fresh. A lock without a fresh heartbeat indicates startup, a stall, or failed health reporting and needs investigation. Status metadata is owner-only and contains no message bodies, credentials, attachments, or full exception diagnostics. Standard output/error go to `/dev/null` to prevent provider diagnostic leakage. Startup before state access can fail without creating a heartbeat; inspect registration and private configuration locally.

The initial direct-Python LaunchAgent had Slack connected but could not read Messages, although the interactive probe passed. The new dedicated native app gives Sebastian its own macOS permission identity without granting broad Python access. The LaunchAgent inherits the logged-in user's graphical session, not Terminal's privacy grants. Verify Messages read access from the actual service process; macOS Full Disk Access, Automation, and Accessibility may require approval for its actual executable identity. A successful interactive probe does not prove these LaunchAgent permissions. Browser/native runtime tools retain their own host authorization requirements. Grant Full Disk Access to the new Sebastian Native app only with the required user approval. Its Automation approvals must also be observed; do not reuse or modify the legacy Sebastian app. Ad-hoc signing is local identity, not Apple distribution notarization. `messages_readable` proves only read access, not sending or Automation permission. Slack connectivity proves only the connection, not end-to-end receipt/delivery.

The Mac must be logged in, awake, online, and available to its desktop tooling. Login startup is not a system boot daemon. Physical sleep pauses execution; recovery and stale-request expiration remain the engine's responsibility. Installation and healthy status alone do not satisfy S7: controlled owner Slack and Messages receipt-to-final delivery, a computer/Chrome request, and the independent V7 inspection remain required.

Verify these control boundaries without activation or live sends:

```sh
.venv/bin/python -m unittest discover -s tests -p test_daemon.py -v
```

### Latency follow-up

The daemon now hosts `RoutedRuntime`: the existing serialized normal native session plus a separate image-only native session with no desktop/personal tools. Their IDs persist separately in `sessions.json` and `image-sessions.json` under private state. Two reserved workers claim metadata-only normal/image lanes; exact conversation/thread ordering crosses both lanes. Shutdown cancels and closes both runtimes. The public health `runtime_pid` refers to the normal runtime when started. See `docs/performance.md` for measurements and restrictions.
