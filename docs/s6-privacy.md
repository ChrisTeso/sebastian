# S6 privacy review

Reviewed the current installation on 2026-09-12 without provider calls, live sends, transcript reads, or legacy implementation reuse. This is an implementation-stage security review; the fresh V6 verifier remains required.

Run `python3 scripts/check_privacy.py` and `python3 -m unittest discover -s tests -p test_privacy.py`. The checker is read-only and emits only booleans and counts. It rejects missing, relative, symlinked (including ancestors), multilink, non-owner, or broadly readable credential files and unsafe state/restricted directories. It checks the actual installation; it is not an additional runtime admission layer. Tests exercise unsafe filesystem fixtures and verify secret matching reports counts only.

## Actual installation

The installation file, Slack app/bot credentials, and unused Agents executor key are mode0600, owned by the current user, under ignored `.private`. Its root and configured state/restricted workspace are mode0700. State/restricted paths are distinct from the owner workspace and remain inside `.private`. Exact credential byte comparisons found **zero** matches in current Git-listed ordinary tracked/untracked artifacts; no private files were tracked. The checker does not print identities, tokens, filenames matched to credentials, or message content. This does not scan historical Git objects or prove absence of every possible private string.

The recovery archive remains outside the workspace at `/Users/teso/.codex/recovery/sebastian-20260912T162029Z`. The directory is0700 and its immediate files0600. Only filesystem metadata was examined in this review. Prior V1 supplies archive integrity and clean-baseline provenance evidence; no archived implementation was opened or imported. Active runtime code selects `/Applications/ChatGPT.app/Contents/Resources/codex` and has no OpenClaw or recovery source dependency.

## Enforcement and disclosure

`policy.py` authenticates registry-signed adapter events. Slack owner authority requires the configured exact team/user pair; Messages requires the exact configured owner/account pair, `is_from_me`, and verified account metadata. Group history, names, quoted claims, and attachment text do not confer authority. Session fingerprints bind channel, account, conversation/thread, sender, permission configuration, and audience. Restricted threads have no execution environments/capability roots, disable configured apps/plugins/MCP servers and personal memory, and fail closed on unexpected MCP tools. Native subprocess environment inheritance is limited to basic OS variables; provider token environment variables are not forwarded. The native process still runs as Chris and relies on Codex's proven per-thread isolation; it is not a separate OS user/container.

The **actual installation has `owner_group_replies=true`**. Under Chris's explicit latest audience authorization, an authenticated owner group request retains owner tools and receives its final reply in that same group/thread. This intentional authorized disclosure is not a privacy failure. Nonowners cannot enable it or reuse owner sessions. Avoidance of unrelated private details remains a model instruction and owner responsibility when requesting sensitive work in a group; the service is not a semantic data-loss-prevention classifier.

`ledger.py` stores provider identifiers/timestamps, statuses, checkpoints, receipts, and HMAC fingerprints, without raw prompt/reply bodies. Session metadata stores opaque session IDs keyed by scope hashes. Images/prompts remain ephemeral in Sebastian wrappers. Native stderr is discarded and Slack client logging is disabled in the production engine. Runtime/provider failures use fixed messages rather than raw exceptions. Other provider/application retention is outside this local metadata guarantee.

## Native retention limitation

Persistent native Codex transcripts are explicitly retained separately from Sebastian metadata and documented in `docs/session-runtime.md`. Only the **five exact synthetic S5 session files** referenced by `.private/s5-runtime/sessions.json` were inspected, using `stat`; no transcript content or unrelated session was read. All five are0644, with `.codex` and `sessions`0755 and home0750. They therefore are **not protected by an owner-only directory**; home-group access remains possible subject to host ACLs. ACLs and membership were not assessed. The checker reports `synthetic_native_sessions_owner_protected=false` separately from installation checks, and does not call this owner-only or zero-retention storage.

The S6 benchmark starts with `os.umask(0o077)` before spawning the native runtime. Independently statting its exact session ID from `.private/s6-latency/1789251247168900000/sessions.json` found **one native transcript at0600**, confirming that this installed native version honors the per-service umask. No transcript contents were read. The checker now separately checks all exact S6 benchmark session references and requires their files to be owner-only.

**Required S7 launcher setting: set umask077 before creating state or spawning Codex**, and verify a newly created deployed Sebastian transcript is0600. This is a bounded per-service remedy for future private traffic; no global Codex permissions changes or repair of older harmless synthetic probe transcripts are needed. S7 must preserve this setting and verify it rather than relying on the launching shell's default. Native transcript retention remains accepted and separately documented; broad access to new private transcripts is not accepted.

## Scope limits

Actual configured paths pass the filesystem audit. Runtime `load_settings` validates absolute paths but does not independently reject all unsafe ancestor/path choices accepted by an administrator; the checker catches those choices for this installation. No generic configuration-hardening changes were made to frozen S2–S5 code. This review provides neither new live channel identity proof nor operational service-installation proof; those belong to the existing channel evidence and S7.
