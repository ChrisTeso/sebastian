# Persistent native runtime

`sebastian.session_runtime.SessionRuntime` composes the verified production AppServer through a private subclass. It uses the same installed Codex native binary, final-answer event handling, pending-event buffer, external permission checks, and refusal of fabricated approvals. It does not create visible desktop tasks or launch another reasoning runtime.

API: construct with an owner-only metadata file path and optional native `binary`/`model`; call `respond(key, profile, prompt, reader=None, timeout=180)`. `stage_images(list_of_image_data_urls)` stages images for exactly one subsequent call on the **same calling thread**. Stage and respond together within a queue worker. Invalid stages clear earlier images. Images are bounded, converted directly to native `{type: image, url: data_url}` turn inputs, and cleared after success or failure. They are never written by this wrapper.

A process-global desktop mutex serializes every SessionRuntime call, including native computer tools. This covers the Sebastian process, not unrelated desktop applications or other OS processes. The timeout includes lock wait, native initialization, MCP inspection, and response time. Cleanup may add native interruption (up to two seconds) and bounded native shutdown time. `last_timings` reports queue wait, runtime initialization, remaining runtime/cleanup, and total seconds; these are metadata, not an S6 latency benchmark.

`cancel()` may run on the control thread. It sets an event and reports whether a request is active. Only the request thread reads protocol messages and performs native interruption. Cancellation does not pretend that already-started actions were rolled back. Cancellation while waiting for a mutex does not cancel that queued request; the host queue owns queued-job cancellation. `close()` signals cancellation and shuts down the native process after the active worker exits.

The durable file stores only a map of SHA-256 hashes of conversation key, complete permission-profile fingerprint, and model choice to native thread IDs. The directory is mode 0700 and atomic file replacements are mode 0600. Profile instructions append the host's authorized final-delivery convention without replacing the original boundaries. On a new explicit request after process failure or service restart, `thread/resume` reconnects the same native thread with the same supported profile config, sandbox, and developer instructions. Resume failure surfaces as failure; it does not silently create a different session. The original native environments and dynamic-tool descriptor belong to that persisted thread. The live restricted profile inventory is checked again before every new turn.

No failed or uncertain `turn/start` is automatically replayed. Any response failure discards the transport. The host must mark uncertain computer actions for reconciliation instead of retrying them blindly. A later newly authorized user request can continue the native session.

## Retention

This wrapper does not persist prompts, replies, images, tool output, or credentials, and drops native stderr. **Native Codex persistent session storage retains conversation transcripts and may retain image/tool content under the user's Codex home.** Native session IDs depend on that storage for resume. This is not a zero-retention runtime; native retention is separate from Sebastian's minimal queue/metadata files. No transcript scraping is needed for recovery.

## Verification

Run `python3 -m unittest discover -s tests -p test_session_runtime.py` for one-shot/thread-local images, failure clearing, metadata permissions, cross-profile separation, cancellation, native image schema, resume fields, and serialization fixtures.

Run `python3 scripts/probe_s5_runtime.py` for harmless live native calls: generated red PNG interpretation, explicit reconnect after killed runtime, restart continuity, active native cancellation then same-session continuity, and concurrent responses. It writes only boolean results and timings to `.private/s5-runtime/probe-results.json` (0600), plus opaque session IDs. It performs no channel sends, browser actions, private file reads, or credential extraction. This is functional S5 evidence; the S6 measured latency and failure matrix remains separate.
