# S5 service integration

`Engine` connects the verified adapters and policy to the persistent `SessionRuntime`. Socket callbacks accept only provider-bound event metadata before acknowledgement. Text stays in memory; queued work is rehydrated from the exact Slack message/thread or Messages GUID/chat using trusted provider reads. Changed account namespaces are rejected. Every Slack rehydration uses a fresh validating adapter, so transient downstream image/provider failures cannot poison an in-memory seen set; only the durable queue controls processing deduplication. Messages jobs commit before the cursor advances, so a crash between those commits is safely deduplicated. The listener and worker are separate; SDK reconnection uses capped exponential backoff, while request retrieval retries are limited to three before any model action starts.

Private installation configuration retains owner_group_replies=true. Group answers stay in the original group/thread. Optional named teammate grants live in owner-only `.private/config/toolbelt-scopes.json` as entries with team_id, user_id, root and documents mapping. Roots must resolve within the local Toolbelt checkout; the S3 curated reader still enforces each document. No grants are currently configured. All other nonowners remain conversation-only.

Images are loaded only from attachments in the authenticated current message or at most ten preceding messages in the exact account/conversation/thread. At most four images and eight MB per image are forwarded as native image input. Slack references resolve through the existing bot API and HTTPS Slack download hosts, without following redirects or forwarding credentials elsewhere. Messages attachments require exact message/chat membership and an approved Attachments-root path opened without symlinks. Unsupported/unavailable images produce an actionable failure rather than a guessed interpretation. Raw images are never written by this loader.

The runtime wrapper serializes desktop use within the Sebastian process and supports cancellation without competing protocol readers. A single deployed consumer is required; S7 installs the process lock and supervisor. The host sends only final answers or actionable failures. Channel formatting splits long output and balances code fences; internal progress-only replies are replaced with a failure. The runtime receives trusted guidance that native messaging tools must not be used merely to deliver its final answer, because the host does that as Sebastian.

## Delivery and recovery

All response parts are fingerprinted before the first send. A durable attempting state precedes the external action. Provider receipts are recorded when available. Slack returns its message timestamp; Messages additionally searches for a matching new verified outgoing local record after the AppleScript call. That is local Messages evidence, not proof that a remote recipient received or read it.

On crash, running model work is interrupted rather than replayed. Pending unsent parts are abandoned because no response body is stored. Attempting sends become uncertain. Recovery reads at most 100 recent messages in each exact uncertain destination and correlates only verified outgoing identity, exact destination/thread, a single keyed body match and a narrow creation-time boundary. Ambiguous/mutated/missing matches remain uncertain and are not resent. Fully confirmed planned responses are retired without another message; partial/interrupted work receives an actionable failure. Messages delivery is not claimed exactly-once. SDK receipt success, content correlation and human-observed delivery remain distinct evidence levels.

Messages self-echo checks use the same keyed fingerprint and scope/time bounds, including pending sends, so a fast outgoing echo cannot recursively trigger a reply. No authority is learned from the echo. Runtime or provider exception strings are not sent or logged; provider loggers are disabled because raw request bodies and signed URLs may appear in diagnostics.

## Storage

The queue contains event references, timestamps, states, retry counts, receipt IDs and keyed fingerprints only. Native session-ID metadata is separate. Both use owner-only files/directories. No prompt, reply body, image, credential or tool output is stored by Sebastian's queue/engine. Native Codex persistent session storage **does retain transcripts and may retain images/tool content**; that is required for native resume and is not a zero-retention guarantee. See session-runtime.md for this distinction and ledger.md for exact state transitions.

## Verification

- `.venv/bin/python -m unittest discover -s tests -v`: 104 PASS.
- `.venv/bin/python scripts/probe_s5_runtime.py`: synthetic native image interpretation, process/service reconnect, active cancellation and same-session continuity, concurrent calls. Private boolean evidence `.private/s5-runtime/probe-results.json`.
- `.venv/bin/python scripts/probe_s5_engine.py`: actual restricted native reasoning through the production engine into an in-memory transport; final-only delivery, duplicate suppression and original group/thread preserved. Private evidence `.private/s5-engine/result.json`. No real channel send occurs.
- Source fixture tests cover provider-bound metadata ingestion, thread rehydration, account changes, unavailable images, raw-body absence, long answers, runtime failure, provider uncertainty, confirmed/partial crash recovery and no model action replay.

S2/S3/S4 frozen artifacts remain unchanged. S6 latency and failure-matrix measurements, S7 installation and live delivery are not claimed by S5. No continuous service is active. Fresh V5 is required before S6.
