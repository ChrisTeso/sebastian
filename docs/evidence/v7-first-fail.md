# V7 independent verification — FAIL

Fresh verifier: `/root/v7`, 2026-09-12. Read actual repository instructions, approved S7 and owner audience addendum, graph protocol, risk-review checklist, original V1 recovery report and all V2–V6 PASS reports. No source or service state changes.

## Blocking finding: Messages self-chat mirror causes a second answer

The first live automated owner-account Messages mention produced two authenticated-ingress records in the same configured owner self-chat: the actual outgoing owner row and the synchronized incoming self-copy. The latter has `is_from_me=false` and unverified owner metadata, so it correctly receives restricted authority, but still executes and delivers another final answer. Both jobs are complete with distinct confirmed outgoing receipts. Their timestamps differ by 0.283 seconds. The model did not escalate authority; the defect is duplicate triggering/delivery.

The main agent identified the duplicate; this fresh verifier independently queried both exact provider rows, actual ledger jobs and receipts. Sanitized evidence: `.private/v7/mirror-defect.json`. No private text was printed; only synthetic marker membership and authentication flags were inspected.

`scripts/check_live.py` verifies the canonical owner job but ignores the mirror's additional response, so its all-green output is insufficient completion evidence. Fix self-chat mirror filtering outside the model and extend live verification to reject extra matching jobs/deliveries. This changes S4 ingress behavior, invalidating the affected S4 artifact gate; obtain fresh independent repair verification and a new controlled live test before final V7.

## Checks that passed before the finding

- All 64 artifacts in S2–S7 manifests matched the frozen candidate. 118 fixture tests passed in 1.723 seconds. `git fsck --full` exited 0, dangling objects only. Privacy checker exited 0; five historical synthetic transcripts remain0644 as disclosed by V6, while all three newly deployed transcripts are0600.
- Actual deployed chain: native app45858 -> daemon45859 -> selected Codex AppServer46191; daemon cwd is this clean checkout. Installed native app strict signature check passed, with designated requirement identifier `com.christeso.sebastian.native`. Installed plist is0600, has umask077, KeepAlive/RunAtLoad and exact `/usr/bin/open -W -n` new-app target. Fresh heartbeat independently showed Slack connected and Messages readable.
- All three old consumer labels (`com.christeso.minime.watcher`, `com.christeso.sebastian`, `ai.openclaw.gateway`) are both disabled and unloaded. No old active consumer or execution dependency in current native/daemon source was found. Recovery archive was not opened or reused; only the independent V1 report was read.
- Source/fixture review confirms kernel singleton lock, signal cancellation, exact-bundle stop, parent-loss detection, fixed-field private health output, and umask before native/state creation. Actual stop/restart evidence was still pending at this report.
- Independent read-only provider requery confirmed the canonical automated owner Slack, Messages and computer jobs each completed with one expected correct-sender/same-destination receipt. The duplicate above invalidates overall Messages success despite that narrow assertion.
- Exact deployed native computer transcript contains actual `mcp__cua_repl.js` Chrome tab creation, returned Example Domain accessibility tree and successful temporary-tab close. This is native tool evidence, not merely an asserted final answer.

Private verification artifacts: `.private/v7/inspection.json`, `live-independent.json`, `native-items.json`, `native-results.json`, and `mirror-defect.json`; all created under umask077. These tests are **automated owner-account requests**, not human-authored inbound proof. Third-party negative scenarios remain fixtures; no third-party live send was performed. Source completion, installation and live behavior are distinct: S7 is not complete.
