# Recovery evidence

Run the deterministic matrix and owned child suspension test:

```sh
.venv/bin/python -m unittest discover -s tests -p test_recovery.py -v
.venv/bin/python scripts/probe_recovery.py
```

Add native App Server continuity and a bounded local tool read:

```sh
.venv/bin/python scripts/probe_recovery.py --native
```

The probe creates a unique run directory under ignored `.private/s6-recovery`
(directories 0700, files 0600). It records counts, statuses, booleans and timing
metadata. Providers, requests, answers and receipts in the fixture matrix are
synthetic. There are no channel sends, service installations, global network
changes, power-setting changes or private computer reads. Native conversation
retention remains subject to the runtime behavior documented in
`docs/session-runtime.md`; the local evidence JSON excludes prompt/answer bodies.

## Observed 2026-09-12

Evidence: `.private/s6-recovery/run-91te8a9j/results.json`.
The two recovery unit tests passed in 1.402 seconds. The standalone native probe
passed all eight matrix scenarios and native checks.

| Case | Mechanism and observed result |
| --- | --- |
| Fresh start | Actual Engine with a new Ledger has no work. A new fixture event runs once; duplicate acceptance and reopening do not replay it. |
| Temporary network/provider outage | Source fixture raises `ConnectionError` twice before runtime execution. Actual Engine schedules 1-second then 2-second delays; Ledger clock is advanced deterministically. Three fetches, one runtime call, one final fixture delivery. |
| Exhausted outage | Four failed fetches exhaust three retries. No runtime execution; one actionable interruption response. |
| Runtime failure | Fixture runtime raises after recording one invocation. Engine emits actionable failure, suppresses exception details, and does not replay after restart. |
| Stale command | Ledger clock advances 601 seconds. Zero fetches and zero runtime calls; one interruption response. |
| Service orchestration restart | Actual Engine and Ledger are closed/reopened. A running job becomes interrupted with no runtime replay. This tests orchestration restart, not an installed LaunchAgent. |
| Partial delivery restart | Confirmed/attempting/pending parts recover as confirmed/uncertain/abandoned. Engine adds one interruption notice; old parts are preserved and never automatically resent. |
| Completed delivery restart | A confirmed part with interrupted job bookkeeping completes without another send. |
| Process suspension | Only the probe's Python child receives SIGSTOP; `waitpid` confirms stopped state. SIGCONT after 1.25 seconds resumes the actual Engine. With a 1-second fixture stale threshold, zero fetches/runtime calls occur and the queued action expires. Observed total 1.304 seconds. |
| Native fresh/warm | Actual SessionRuntime/App Server completes a fresh request and recalls its synthetic token on a warm request. |
| Native executor reconnect | Kill only the probe-owned idle App Server. A subsequent explicit request reconnects and recalls the same synthetic token. This does not prove automatic replay of an in-flight request. |
| Native runtime restart | Close/reopen SessionRuntime against the same session metadata. Explicit next request recalls the token. All owned runtime processes were cleaned up. |

The native local tool request read one allowlisted synthetic document through the
production `ToolbeltReader`. Its answer was verified against content absent from
the prompt. End-to-end elapsed time was **7.083 seconds**, with runtime/model/tool
time **7.08249 seconds**, startup **0 seconds**, and lock queue wait **0.0000045
seconds**. This is one local tool request, not a representative tool-heavy latency
distribution. It performs no provider ingress or channel delivery; those are not
included in this measurement. Warm no-tool measurements are in
`docs/performance.md`.

## Evidence limits and remaining gate

**Physical Mac sleep/wake observed with Chris's explicit authorization.** Evidence: `.private/s6-recovery/physical-04usmm61/results.json`. macOS power log records sleep at 2026-09-12 15:35:55 and wake at 15:36:16 local time. The standard administrator prompt authorized a one-time wake timer and sleep command; permanent sleep settings were not changed.

The first evidence collector hit a non-UTF8 byte in the OS power log after wake. The parser was corrected to decode with replacement, then `scripts/probe_sleep_wake.py --resume-evidence .private/s6-recovery/physical-04usmm61` completed the checks without another sleep. The same native conversation recovered its pre-sleep synthetic token using persisted session metadata after the first probe's cleanup closed its process. The queued synthetic command expired with zero source fetches and zero runtime calls; only the fixture interruption notice was produced. This probe uses a 10-second stale threshold to keep the physical test short; the production 600-second threshold is separately tested in the recovery matrix. Provider transport remains synthetic, with no messages sent. Verification elapsed fields cover the post-wake collection, not sleep duration. Post-wake native continuation took 4.791 seconds including 0.129 seconds startup.

`python3 scripts/probe_sleep_wake.py` initiates a new physical test and requires deliberate owner coordination. Do not rerun it unprompted. The optional `--resume-evidence` mode performs read-only power-log inspection and synthetic queue/native continuation against an existing private test directory without another sleep. Existing OS event timestamps are evidence of the physical cycle, distinct from the SIGSTOP fixture above.

Network failures are injected at the provider interface using the real
orchestration/ledger. They establish bounded retrieval retry and recovery, not a
live Slack reconnect during a physical network outage. Existing receiver checks
and eventual live service evidence remain distinct.

These are implementer-produced checks; a fresh V6 verifier must independently
reproduce the relevant cases before the gate passes.
