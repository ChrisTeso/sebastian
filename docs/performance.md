# Response measurements

Run `.venv/bin/python scripts/benchmark_replies.py` to measure at least 20 warm no-tool requests through the production Engine, Ledger and SessionRuntime. Ingress and delivery use a fixture transport; native owner reasoning uses the selected installed Codex runtime with its default model. These are not live Slack/Messages network measurements.

The completed sample at `.private/s6-latency/1789251247168900000/results.json` contains all 20 warm runs plus one cold request. Every answer was correct and no tool items occurred. Warm median: **3.701 seconds**; nearest-rank p95: **5.470 seconds**. The under-5-second median and under-10-second p95 targets passed. Cold request: **4.332 seconds**, including **0.088 seconds** native startup.

Warm stage medians: ingress acceptance 0.00061 seconds, queue wait 0.00125 seconds, conversation fixture fetch 0.00012 seconds, runtime startup 0 seconds, model/tool interval 3.696 seconds, fixture delivery 0.00240 seconds. Queue wait and model/startup intervals are reported separately but are not all disjoint; do not add the medians to reconstruct total latency. The model/tool interval includes native runtime overhead and cleanup, not only inference. No bottleneck fix was justified by this passing sample.

Tool requests vary with provider and desktop operations. Functional image, cancellation, reconnect and concurrency evidence is separately recorded in `.private/s5-runtime/probe-results.json`; these are not part of the no-tool response target. S7 records observed live delivery separately. A sleeping or disconnected Mac cannot provide prompt local replies; commands that expire must not be replayed after return.

A separate S6 allowlisted synthetic local-document tool request completed in **7.083 seconds**, with 7.08249 seconds in the runtime interval, no runtime startup, and 0.0000045 seconds queue wait. Evidence: `.private/s6-recovery/run-91te8a9j/results.json`. This is one measured tool request, not a general tool-latency percentile.

## September 12 latency follow-up

The production host now reserves separate normal/image workers. Only conservatively identified self-contained owner image requests enter the image lane, using a native runtime without local execution, personal apps or MCP tools. Other requests retain the normal runtime and desktop lock. Thus a slow image in one conversation need not delay a normal reply in another. Messages within the same exact conversation/thread remain ordered across lanes; mixed image-plus-action requests stay on the normal path. More complex tool work still serializes.

The model remains inherited GPT-6 Astra. A small set of short routine prompts (greetings, arithmetic, exact-output and short language edits) uses low reasoning; other requests retain medium reasoning. No global account model setting is changed. The historical 20-run benchmark above is not a new benchmark of this change. Live follow-up timings are recorded separately in `docs/progress.md`.

Final checks: a text reply in another conversation completed while image generation continued (14.754 seconds before the image receipt). Final direct Messages arithmetic response: 5.523 seconds end to end, including 0.099 seconds queue wait. Current attachments always load; previous image pixels are omitted only for narrowly self-contained nonvisual requests. Full evidence and limits: `docs/evidence/latency-live.json`. Model/provider time remains the dominant interval; these individual observations do not establish a new percentile or guarantee every response time.

## Model delegation follow-up

Luna now answers simple requests directly; routine tool/research work escalates to Sol and complex/consequential work to Astra. A four-case matched native sample measured Luna median 2.924s vs Astra 3.794s; escalation routing added median 3.183s. A real owner Slack Luna reply took 8.378s including channel/context overhead. Do not treat the synthetic improvement as a guarantee for live or escalated requests. See `docs/model-routing.md` for routing, labels, and full evidence.
