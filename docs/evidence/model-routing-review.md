# Multi-model routing focused independent review

Date: 2026-09-12

Verdict: **PASS within the reviewed implementation and local-test scope.** No concrete authority, cancellation, model-label, session-separation, or schema regression was found. This is not a service activation or live channel-delivery gate.

## Scope and evidence

Reviewed `sebastian/routed_runtime.py`, `model_router.py`, `session_runtime.py`, `service.py`, `outbound.py`, and the runtime failure handling in `engine.py`, with supporting reads of the runtime policy and native adapter. No implementation edits, service restarts, or messages were performed by this reviewer.

- Luna runs under a conversation policy with no reader, environments, personal MCP/apps, native shell, image generation, view-image, or sleep tools. Structured output accepts only `answer`, `sol`, or `astra`; duplicate fields, extra fields, mismatched answers, malformed JSON, and image outputs are rejected.
- Sol/Astra escalation receives the original policy, request, staged images, and approved reader. Routing cannot grant requester authority or select a delivery audience.
- A shared request deadline and external cancellation event reach the stages. Cancellation after routing prevents escalation. Only the tool-disabled Luna stage has a fallback; Sol/Astra failures propagate without replay.
- Runtime instances bind explicit model IDs; metadata paths and session scope hashing separate models. Fresh native threads disable model fallback, and turns explicitly select their model and optional output schema.
- The existing isolated image lane selects Sol. Host-generated labels cover direct Luna answers, escalated replies, and image-only replies. Host-only operational messages that invoke no model are outside model attribution.

Independent verification command:

```sh
PYTHONPATH=tests:. python3 -m unittest test_model_router test_routed_runtime test_session_runtime test_service test_outbound test_engine
```

Result: **45 tests passed**, including all 10 updated routed-runtime tests and the host-label test for an image without a caption. These cover authority/reader preservation, cancellation between stages, malformed-router fallback, no replay after Sol failure, image-lane confinement, and concurrent image/text input separation.

An additional in-memory adapter/service check passed: fresh thread creation sends `gpt-5.6-luna` with `allowModelFallback=False`; turn creation sends the same model and `ROUTER_SCHEMA`; the service appends exactly `Model: Sol (routed by Luna)` to a synthetic text reply. This check used mocks and did not start the native runtime.

## Remaining live verification

The parent reported the complete 165-test suite passing and a native simple request producing the correct answer on Luna in 2.94 seconds. Those results were not independently rerun by this reviewer. The structured native routing evaluation and matched Astra comparison were still running at handoff.

Retain separate evidence for native route-selection accuracy, actual native tool isolation, service activation, and authorized live channel delivery with labels, including generated images. This focused review does not establish those live gates or invalidate their existing pending status.

## Follow-up review: native results and small instruction/notice changes

The reviewer inspected `.private/model-routing-eval/results.json`, the probe script, and the subsequent changes to router instructions and host notices. The bounded **PASS remains valid**.

- The saved native evaluation records 11 of 12 exact route expectations satisfied. The twelfth requested a fabricated private bank balance; Luna chose `answer`. The parent inspected the actual synthetic output and confirmed a transparent refusal, then annotated the result as accepted. The artifact preserves `correct: false` and the original failed exact-route aggregate rather than rewriting the original result. This is an accepted safe refusal, not a claim of 12 exact route matches.
- Router instructions now explicitly permit a brief transparent refusal to fabricate facts or pretend an action occurred. This does not change the JSON contract or grant tool authority. The native evaluation preceded that instruction clarification and was not rerun for this review.
- The recorded simple-response medians are 2.924 seconds for Luna and 3.794 seconds for matched Astra, with four synthetic cases each. Recorded Luna routing overhead has a 3.183-second median. No monitored native tool items were observed. These small synthetic results do not establish production latency, p95, or the impossibility of tool access.
- Host-only notices for private-conversation redirection, stale/interrupted requests, unavailable images, cancellation, and failures with no attempted model now append `Model: none (service notice)`. Runtime failures with attempts continue to identify the attempted models.
- The reviewer independently reran `PYTHONPATH=tests:. python3 -m unittest test_model_router test_routed_runtime test_service test_engine`: **32 tests passed** after the small changes. The parent reports the complete suite remains at 165 passing tests.

Live provider-visible labels and the Sol image delivery check remain pending outside this review. Native checks were not rerun, and this reviewer performed no service restarts or message sends.
