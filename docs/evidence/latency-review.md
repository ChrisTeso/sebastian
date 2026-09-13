# Focused latency risk review

2026-09-12 — Independent reviewer: **PASS** for the scoped implementation and synthetic tests below. Native concurrency and owner-only live service delivery remain separate parent-owned checks.

Reviewed `sebastian/routing.py`, `routed_runtime.py`, `session_runtime.py`, `engine.py`, `ledger.py`, and `daemon.py`, plus their focused tests. No additional blocking concurrency, authorization, cancellation, or recovery regression was found. The separately discovered leased-job expiration ownership race is repaired: only the owning worker or startup recovery releases active job ownership.

Original findings and corrections:

- Image requests combined with reminders or alarms incorrectly selected the restricted image runtime. Routing now rejects action conjunctions, relevant outside-action words, and multiple sentences; regression cases retain the normal runtime.
- Explicit “Think deeply…” requests incorrectly selected low reasoning. Reasoning now defaults to medium, with low restricted to short routine patterns; the regression case selects medium.

Verified invariants: exact conversation/thread jobs block across lanes; unrelated normal work progresses while image work is active; normal SessionRuntime execution retains the shared desktop lock; image execution uses a separate lock and a restricted profile without execution environments, capability roots, apps, configured plugins, or MCP servers; staged images remain thread-local; cancellation reaches both runtimes; worker-local recovery preserves other active work.

Reproducible checks (47 tests, all passing):

```sh
python3 -m unittest discover -s tests -p 'test_routed_runtime.py'
python3 -m unittest discover -s tests -p 'test_engine_concurrency.py'
python3 -m unittest discover -s tests -p 'test_concurrent_ledger.py'
python3 -m unittest discover -s tests -p 'test_session_runtime.py'
python3 -m unittest discover -s tests -p 'test_engine.py'
python3 -m unittest discover -s tests -p 'test_daemon.py'
```

This review did not restart services, send provider messages, or independently repeat native image-generation proof. Routing is a conservative scheduling heuristic; permission enforcement remains outside the model.

## Image-history loading follow-up

2026-09-12 — **PASS** for the bounded follow-up affecting `needs_image_history`, `ConversationImages.load_context(include_history=...)`, and the two channel fetch callsites. Current-event attachments load before the optional history return. Ambiguous and visual follow-ups retain prior images; only fully matched greetings, numeric expressions, and narrow literal-response requests skip prior image loading.

The initial output-format prefix incorrectly treated “Reply with only the number of dogs” as self-contained. Full-match arithmetic and a narrow literal-response grammar correct that finding; regression cases cover implicit visual counting, ordinal descriptions, and mixed requests. No additional blocking finding remains in this delta.

Re-ran `test_routed_runtime.py` (7), `test_images_formatting.py` (6), and `test_sources.py` (4): **17 tests passed**. Native service restart and simple-reply timing verification remain parent-owned; this reviewer performed no provider actions.
