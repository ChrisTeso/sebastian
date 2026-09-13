# V4 mirror repair independent verification — PASS

Fresh verifier `/root/v4_mirror`, 2026-09-12 (`fork_turns: none`). Reviewed actual AGENTS.md, approved S4/S5/S6 requirements and owner-audience addendum, graph protocol, risk-review checklist, current adapter/tests/docs and V7 duplicate evidence. No implementation changes or live channel sends. This report revalidates the changed S4 ingress requirement and affected fixture integration; historical capability/performance reports remain historical evidence.

## Findings and proof

No blocking correctness or permission regression found in the scoped repair. The incoming-mirror predicate requires all four conditions: incoming direction, direct/non-group classification, an exact configured owner-private chat GUID, and sender equality to a frozen trusted login alias after stripping only E:/P:. It skips ingress before signing/enqueueing; it never changes sender verification or grants owner authority. Outgoing authentication still independently requires is_from_me and exact account/account-GUID/login matches. Actual other senders and all group paths retain existing mention/authority behavior.

The filter deliberately suppresses any incoming owner-alias row in the verified private self-chat, without text/time matching. This avoids dependence on mirror order or separate provider GUIDs. It depends on correctly frozen trusted owner aliases/self-chat setup; it does not discover aliases from incoming content. History may still include incoming copies as untrusted history, which does not create a second request.

Independently executed:

- `.venv/bin/python -m unittest tests.test_messages -v`: 17 PASS, 0.107s. Includes the outgoing/incoming identical-text regression, genuine nonowner, malformed rows and history, cursor progress, schema fail-closed, relay metadata, readonly SQLite and inert sender tests.
- `.venv/bin/python -m unittest discover -s tests -q`: 119 PASS, 1.712s. Re-exercises actual Engine/Ledger recovery fixtures, exact self-echo/receipt scope, atomic checkpoint/dedupe, provider uncertainty, stale/canceled work, retry/image rehydration, permission separation and final-only delivery. Service-control printed messages are mocked fixture paths, not actual service changes.
- Independent ephemeral SQLite matrix: 15 PASS cases across bare/E:/P: frozen aliases and self-chat, group, nonallowlisted direct chat, multiple participants and unknown chat style. In every case incoming copy precedes outgoing with identical text/distinct GUIDs, followed by a genuine other sender and malformed row. Only self-chat owner-alias copy is dropped; outgoing stays verified; incoming retained elsewhere remains unverified; genuine other sender is retained even in self-chat; cursor reaches all four records and repoll is empty.
- `git fsck --full`: exit 0, dangling objects only.

## Frozen source and scope

All 56 S2–S6 artifact entries match current files, checked after tests and again when writing this report. S2/S3/S5/S6 manifests match historical V6 manifest hashes; S4 has the updated manifest below. No native capability, physical-sleep or latency probes were repeated because those artifacts are unchanged. The full fixture suite supplies affected downstream integration revalidation; it does not newly establish native/provider live behavior.

- S2: 5 artifacts match; manifest `ab93fbb64dbd18f1e7c6a031db544ece365d543d4a27042a835950c0a1d7d4a7`.
- S3: 14 artifacts match; manifest `750853f78586295c824f796014603d48bfac4704c573e0a3c09076e15a568278`.
- S4: 13 artifacts match; manifest `759ed50b50ad0d35da0c32cc71ddf5703d0a1836f502b73c97660c70d925f133`.
- S5: 15 artifacts match; manifest `8ae420636b8b5168decad7a1f0afa500a476c367947bd2ad45e0cf0ed53afe8c`.
- S6: 9 artifacts match; manifest `972fb73bbdccc34c6835e608b6a007ed128280daf18ae40eb8d45e019a6cf8eb`.

Current changed S4 file SHA-256 values:

- `sebastian/messages.py`: `61998b0ef3087fcd999f34fbb3e24d629ca0ad53b5bdc9f594f711653dec3efc`.
- `tests/test_messages.py`: `466af0b57b06b8c39a1e2eebfeddc0ef9c27ac7471f046992f49a26a8d06e0c9`.
- `docs/messages.md`: `0e6a600207fb173ff4bc6a44b2a9d0fcc5918e6471d628ebdf09fa8dacbc546a`.

This PASS permits the repaired S4 candidate to proceed to controlled S7 live retesting. The live checker repair, deployed reload, single-response proof and final V7 remain separate gates; this report does not mark S7 complete. Only this report was written by this verifier. No service start/stop, browser/native capability call, global configuration change, private database access or external sends occurred.
