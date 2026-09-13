# V5 independent verification — FAIL

Verifier: fresh agent `/root/v5`, 2026-09-12. Reviewed repository instructions, approved clean-rebuild S5 and owner audience addendum, graph protocol, and applied risk-review priorities. S6 is not unlocked.

## Blocking finding

**P1 — A transient Slack image-metadata failure silently loses the queued request.** In `sebastian/engine.py:105-107`, `Sources.fetch` calls the shared `SlackAdapter.receive` before loading conversation images. Successful receive marks `(channel, ts)` seen. `ConversationImages._slack` calls `files_info` outside its download-error normalization, so a transient provider/network exception escapes as a generic exception. Engine defers the leased job. On retry the adapter returns `None` because the message is already seen, and Engine discards the job without running the model or delivering an actionable failure. This violates S5 bounded retrieval retry and clear failure behavior.

Independent reproduction: `.venv/bin/python .private/v5/repro.py`. It uses the actual Sources and SlackAdapter with synthetic provider records, and injects one transient image metadata error followed by a successful image result. Observed: `first=transient failure`, `retry_envelope_is_none=true`, `image_loader_calls=1`, `adapter_seen_count=1`. The second available image result is never attempted. Evidence: `.private/v5/repro-result.json`.

Repair must make durable-job rehydration retryable independently of ingress duplicate suppression; add an integrated test that drives Engine through defer, retry, final delivery and duplicate suppression. Obtain a fresh verifier after repair. Do not bypass the error with silent discard.

## Checks actually reproduced

- `.venv/bin/python -m unittest discover -s tests -q`: **103 tests passed**, 0.264 seconds. This suite did not catch the failure above. Output `.private/v5/tests.txt`.
- `git fsck --full`: exit 0; existing dangling objects reported, no integrity error. Output `.private/v5/fsck.txt`.
- Actual native SessionRuntime restart/resume of a restricted session retained synthetic token `v5-amber-318`, refused shell/file/browser requests, and emitted only a userMessage and final agentMessage; zero executed tools observed. No browser state was read.
- Actual native owner SessionRuntime executed one commandExecution to read only the synthetic verifier canary, returning `V5-SYNTHETIC-CANARY-4192`. No channel delivery or private file read was requested. Evidence: `.private/v5/native.py`, `native-result.json`, `native-output.txt`.
- Independently inspected native timeout/cancellation/serialization and session scope code, final-only extraction, image membership/path limits, formatting, queue transitions and source rehydration. Existing synthetic tests passed for those paths. Native image, active cancellation, plugin-read and integrated-engine probes were not additionally reproduced after the blocking finding; do not treat this report as full runtime acceptance.

## Frozen artifact verification

At inspection, every recorded S2/S3/S4/S5 source hash checked matched its manifest; no frozen source was edited by this verifier. Manifest SHA-256 values:

- S2: `ab93fbb64dbd18f1e7c6a031db544ece365d543d4a27042a835950c0a1d7d4a7`
- S3: `750853f78586295c824f796014603d48bfac4704c573e0a3c09076e15a568278`
- S4: `d87fac029d5b8f7b8683326f552c551ae356875b94d94d3a977b2fc685713fc3`
- S5: `fb76646a2dfcfb74ec1e643e699f2f99160b0dc2c388991c10d4044e88c27c01`

Failing reviewed Engine SHA-256: `fbed1bbe101c0f37487cb547ded5a025be089e91220fb4cc28b6ecf1f3c2ff59`. Hash evidence: `.private/v5/hashes.json`.

All fixtures are synthetic. Native processes created by this verifier were closed. No real messages sent, service activation, subscription change, push or publication occurred. Native transcript retention remains distinct from metadata-only queue storage; no exactly-once Messages claim is made. S6 measurements and S7 installation/live inbound proof remain pending.
