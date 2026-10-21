# Freshness upgrade: execution plan

Scope: implement the accepted upgrade proposal in small vertical batches, preserving $0/no-new-account constraints. Faster provider collection and public crypto streaming remain disabled until actual access, limits and hosting usage are verified. The full product proposal is `docs/dashboard-upgrade-plan.md`.

## Work batches

1. **Baseline and recovery:** fast-forward existing remote changes, use a feature branch, record constraints/current state and validation commands. Preserve ignored local notes/data. No worktree or new agent is required.
2. **Quota foundation:** persistent rolling-window request reservations, per-provider/global limits, explicit verification/enablement, failure cooldowns and a guarded HTTP transport. Integrate it into the Pages collector; publish only sanitized usage/health metadata. Existing local API behavior stays compatible.
3. **Freshness and client updates:** versioned publication metadata and a small manifest, content validation, visible-tab/online-aware polling, bounded retries and manual-refresh throttling. Keep immutable archives and daily technical calculations on their original clock.
4. **User-facing evidence:** source budget/freshness panel and a compact change tape. Clearly distinguish collection failures, old observations, offline viewing and overdue publications. Use existing terminal styling.
5. **Verification and handoff:** focused numerical/contract/failure tests, production builds, static/local browser checks and a reviewable commit. Update checkpoint and deployment instructions. Do not increase the scheduled cadence while the verification gate is closed.
6. **Next gated work:** quote/history separation and a data-only publisher, then an hourly measured pilot; only later enable faster schedules, crypto streaming and new release/flow/positioning adapters.

## Token and runtime economy

Checkpoint, 21 October: batches 1–5 are implemented and validated; batch 6 remains gated. Passed: 145 backend + 31 frontend tests, 38 browser checks and both production builds. Source activation and authoritative remote accounting remain incomplete; see `docs/freshness-checkpoint.md` for the next batch.

- Work from the compact checkpoint, file-specific instructions, shared contracts and failing output. Do not reload the whole conversation, repository or reference project.
- Reuse existing dependencies, UI styles and calculation code. Zero LLM calls in the feed path; no dependency installation unless required by a demonstrated gap.
- Keep a single owner of schemas, workflow and integration changes. Do not launch additional agents merely to consume the allowance.
- Run focused tests after each batch; run full regression/build/browser checks once after integration, repeating only affected checks after a new change.
- Keep ordinary tool output short and parallelize independent reads/checks. Never run market-data polling to test quota behavior; use deterministic fixtures and injected clocks.

## Checkpoint protocol

The current resumable record is `docs/freshness-checkpoint.md`; the previous broader history remains in ignored `docs/execution-state.md`. Update the compact record after each validated batch, before a substantial direction change, before yielding and on any actual usage-limit signal. Record branch/commit, changed paths, passed/failed/unrun checks, external blockers and the exact next command/task. Preserve unfinished edits on disk.

The five-hour account allowance has no exposed exact consumption meter or refresh trigger. Use the available time for necessary work, without padding consumption or promising automatic restart. A saved checkpoint supports resumption while this workspace survives; it does not create an external backup or keep processes alive.

## Activation gates

No change to repository visibility, paid services, live provider cadence or Jev access is implied. Provider requests require a verified source policy and a durable reservation ledger. An absent/corrupt ledger, denied source, exhausted budget or unverifiable gate fails closed for governed collection. A mode label must never imply that data was freshly observed. Local deterministic tests and UI work continue independently of network access.
