# Model delegation

Sebastian now uses fixed host-selected native models:

| Stage | Native model | Purpose |
|---|---|---|
| Luna | `gpt-5.6-luna` | Answer simple self-contained conversation/text requests, or choose escalation |
| Sol | `gpt-5.6-sol` | Routine research and tool execution |
| Astra | `gpt-6-astra` | Complex analysis, coding/debugging, consequential decisions, or uncertain routing |

Luna returns a strict JSON answer/route object. It has no personal apps/MCP servers or local execution environment; image generation, shell, image-view and sleep tools are disabled. A simple answer is used directly, avoiding a second inference. Current/external/personal lookups escalate; transparent refusals to fabricate may be answered directly. The router's decision never grants authority: escalation gets the original host policy, approved reader and original request/context. The host still chooses the destination and preserves existing conversation ordering.

Sol/Astra execution is never automatically replayed on error. A malformed or unavailable no-action Luna stage may fall back to Astra under the same original permissions and overall time budget; cancellation prevents escalation. Model IDs and separate private metadata paths are fixed in code, and native model fallback is disabled on thread creation. Global Codex model preferences are unchanged.

Self-contained owner image requests recognized by the existing conservative classifier use the reserved image-only Sol runtime directly. Mixed/ambiguous image requests use Luna routing and the normal lane. This classifier can conservatively keep benign image requests in the normal lane; it never broadens permissions.

The host appends `Model: Luna`, `Model: Sol (routed by Luna)`, or the corresponding Astra label. Image responses include the model label and `image tool`; the label identifies the assistant/orchestration model, not an asserted underlying pixel-generation model. Host-generated notices say `Model: none (service notice)`; runtime failures list attempted models.

Verification: 165 fixture tests, fresh independent review, 12 native routing cases (11 expected routes and one safe fabrication refusal), matched four-case simple-answer timing, and live owner-only Slack Luna and Messages Luna-to-Sol image delivery. See `docs/evidence/model-routing-live.json` and `docs/evidence/model-routing-review.md`. The matched medians were Luna 2.924 seconds and Astra 3.794 seconds; routing before escalation added a median 3.183 seconds. Small samples do not establish a percentile or guarantee faster total tool requests.

Every outgoing text part ends with the host-added signature `– Sebastian, Chris's AI Assistant`. The model label appears above the signature. The same signature is used for image captions and non-model service notices.
