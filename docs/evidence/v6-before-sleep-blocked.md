# V6 independent verification — BLOCKED

Verifier: `/root/v6`, fresh context. Date: 2026-09-12. Applied the approved clean-rebuild plan, actual repository AGENTS.md, owner-audience addendum, graph protocol and risk-review checklist. No implementation files changed.

## Blocking finding

**Physical Mac sleep/wake remains unobserved.** S6 explicitly requires it. The reproduced SIGSTOP/SIGCONT test is owned-process suspension, not Mac sleep, network reattachment, or host permission restoration. There is no owner acceptance change waiving that requirement. Automated checks below passed, but this report does **not** unlock S7.

To finish this gate, coordinate a real sleep/wake cycle with Chris, observe OS sleep/wake timestamps, verify the existing native session can respond after wake, and prove an expired queued synthetic action does not execute. Use no channel sends or real private commands. Obtain fresh independent verification of that evidence and ensure frozen hashes still match. Re-running every passing benchmark is unnecessary unless artifacts change or the new evidence exposes a regression.

## Independently executed evidence

- `.venv/bin/python -m unittest discover -s tests -v`: **109 tests PASS**, 1.672 seconds. Includes identity/isolation, routing, image/provider failures, queue/delivery transitions, native transport, serialization, cancellation, privacy fixtures and recovery.
- `git fsck --full`: exit0, dangling objects only; no corruption.
- `.venv/bin/python scripts/check_privacy.py`: exit0 before and after probes. Actual configuration/credentials/state/restricted paths pass owner/mode/link checks; zero ordinary-artifact credential matches and zero private files tracked. Recovery archive metadata is owner-only and outside the runtime workspace. This is current working-artifact matching, not an exhaustive historical/private-string scan.
- `umask 077` followed by `.venv/bin/python scripts/probe_recovery.py --native`: exit0. Fresh independent evidence: `.private/s6-recovery/run-sxmivns7/results.json`.
- `.venv/bin/python scripts/benchmark_replies.py --runs 20`: exit0. Fresh independent evidence: `.private/s6-latency/1789251868245310000/results.json`.
- Additional direct checks saved in `.private/v6/independent.json` and `.private/v6/benchmark-check.json`: real AppServer environment excludes injected synthetic token/session variables and contains only the seven permitted OS variable names; nine recovery databases contain none of four synthetic request/answer/error/partial-body canaries; recovery evidence files have no group/other mode bits; independently recalculated benchmark percentiles and checked stage ordering and exact native transcript mode without reading transcript contents.

## Recovery results and proof boundaries

All eight actual Engine/Ledger fixture scenarios passed:

- Fresh start, duplicate acceptance and reopening: one runtime invocation and one final fixture delivery.
- Temporary source outage: two injected ConnectionErrors, 1s/2s bounded retry schedule, three fetches, one runtime call and final delivery.
- Exhausted source outage: four failed fetches, three retries, no runtime call, one actionable interruption response.
- Runtime exception: one attempted invocation, sanitized actionable failure, no replay after restart.
- 601-second stale event: no fetch or runtime call; interruption response only.
- Restarted running job: interrupted, no model replay.
- Partial delivery restart: confirmed/attempting/pending recover to confirmed/uncertain/abandoned; no original part replay.
- Completed delivery restart: retires bookkeeping with no extra send.

The owned child was observed stopped and resumed after 1.277 seconds; its one-second-threshold queued action expired with zero fetches/runtime calls. This is simulated process suspension only.

Actual native SessionRuntime/AppServer completed fresh and warm requests, retained a synthetic token after killing only its idle owned runtime process, and retained it after closing/reopening SessionRuntime against its metadata. A subsequent explicit request drives reconnect; this does not authorize or prove replay of an interrupted action. All owned probe runtimes closed. A real allowlisted synthetic local-document tool call returned its independently checked code in **7.551s**, of which 7.55064s was the runtime interval. This is one bounded tool observation, not a tool-heavy percentile distribution.

Network outage evidence is injected at the provider boundary using real orchestration, not a physical network interruption or live Slack reconnect. Service restart evidence is Engine/Ledger reopening, not an installed LaunchAgent. No live channel sends occurred.

## Independently reproduced response measurements

21 total requests: one cold plus **20 warm**, actual native owner runtime with its default model, fixture ingress/delivery. All answers correct; no completed tool items. Warm median **3.733s**, nearest-rank p95 **4.991s**; both original targets pass. Cold request **4.077s**. The native recovery probe ran concurrently for part of this sample; no exclusivity or live channel latency is claimed.

Warm stage medians:

| Stage | Seconds |
| --- | ---: |
| Fixture ingress acceptance | 0.000763 |
| Queue wait | 0.001534 |
| Fixture conversation fetch | 0.000141 |
| Native startup | 0 |
| Model/runtime/tool interval | 3.726572 |
| Fixture delivery | 0.003101 |

Independently recomputed values equal the script summary. All recorded elapsed/engine/model/startup intervals are nonnegative and correctly nested. Stages overlap; their medians must not be added to reconstruct a total. Runtime interval includes native overhead/cleanup. Result SHA-256: `a26280b69b8abc5b9667f705b648634cc084855724125db1519cf2186b8ad28b`.

## Privacy/security assessment

Inspected actual runtime policy, AppServer, SessionRuntime, Engine, Ledger, checker and recovery/benchmark sources plus existing V3/V5 evidence. Restricted configuration removes execution environments/capability roots, configured apps/plugins/MCP servers, personal memory and project instructions; unexpected MCP inventories fail closed. The full suite re-exercised those boundaries. Native OS process still runs as Chris, so this uses the previously verified native per-thread isolation, not a separate OS account/container. Complete policy fingerprints partition durable sessions. Provider secrets are excluded from the child environment. Native stderr is discarded; production Slack client logging is disabled; errors are sanitized. Ledger stores metadata and HMAC fingerprints, not raw bodies.

Authenticated owner group replies intentionally retain owner tools and the originating audience under Chris's explicit authorization. Nonowners cannot confer that authority. Semantic avoidance of unrelated sensitive information is not a universal host data-loss-prevention mechanism.

Native Codex retains transcript content separately. Independently located the new benchmark's exact native session file by its opaque session ID and confirmed **0600**, without reading it. Checker finds both S6 benchmark transcripts owner-only. Five older harmless synthetic S5 transcripts remain0644 and are not owner-protected by ancestors; that disclosed historical probe condition is not misrepresented as private storage. **S7 must set per-service umask077 before spawning native runtime and verify a newly deployed transcript0600.** No global transcript mode changes are required or were made. Recovery archive contents were not reused or opened; current active sources point to the installed Codex binary and contain no legacy/recovery execution dependency.

No additional actionable defect was found in this bounded S6 review. The physical sleep/wake evidence gap remains the sole V6 blocker; installation and live inbound/delivery proof remain separate S7 requirements.

## Frozen-artifact verification

All 55 recorded files across S2–S6 matched their manifests before and after checks (5/14/13/15/8 respectively). Manifest SHA-256 values:

- S2: `ab93fbb64dbd18f1e7c6a031db544ece365d543d4a27042a835950c0a1d7d4a7`
- S3: `750853f78586295c824f796014603d48bfac4704c573e0a3c09076e15a568278`
- S4: `d87fac029d5b8f7b8683326f552c551ae356875b94d94d3a977b2fc685713fc3`
- S5: `8ae420636b8b5168decad7a1f0afa500a476c367947bd2ad45e0cf0ed53afe8c`
- S6: `86a5d7bc298e29411b51194ab4b63147e129100ea05827f90361bd0f2fe929ae`

Changes are limited to this report and ignored owner-only synthetic probe evidence. No provider/channel sends, service installation, global configuration/network/power changes, push, merge or subscription changes.
