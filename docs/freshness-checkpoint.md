# Freshness upgrade checkpoint

Updated: 21 October 2026. Branch: `feat/freshness-controls`, based on remote `main` at `dfc343d`. Validated implementation: `7301e757c2ced9e0a5b0a31495e7624a0013fc50`, committed and pushed to `origin/feat/freshness-controls`. The user approved the OpenStock-inspired proposal and requested token-efficient edits with recovery checkpoints.

## Read first on resumption

Read this file, `docs/upgrade-edit-plan.md` and `docs/collection-controls.md`; then run `git status --short --branch` and `git log -3 --oneline`. Consult `docs/dashboard-upgrade-plan.md` only for product detail. Preserve ignored local notes, original prompts and databases. Earlier history is in ignored `docs/execution-state.md`.

Constraints: $0 incremental cost, no new accounts, public machine-readable APIs, existing GitHub account only, no paid model calls or Jev access. Retain the twelve-reference universe, completed-session baseline, separate observation clocks and immutable archives. No sizing, execution or inferred capital flows. Exact five-hour quota consumption and refresh triggers are not exposed; do useful work without padding runtime or promising an automatic restart.

## Completed implementation

- `services/market/governor.py` and disabled `config/source-policies.json`: atomic persistent request reservations, global/provider/verified rolling windows, explicit initialization, fresh verification, clock checks, Retry-After cooldowns and persistent access-denial gates. Failures, redirects and cancelled work retain reservations. Out-of-order responses cannot erase verification requirements or longer cooldowns. HTTPX hooks guard requests before proxy routing.
- `services/market/export_pages.py`: governed Pages collection, sanitized captured budget metadata, versioned/hash-bound publication generations, preceding-generation retention and a factual change tape. Existing public report privacy/digests and legacy files remain. The local API retains its previous collection behavior.
- Shared contracts and client validation: bounded reads, safe paths, SHA-256 checks, generation consistency and CDN rollback protection. Unchanged generations fetch only the manifest.
- Dashboard: shared manual/automatic cooldown, hidden/offline pausing, bounded backoff, retained validated views after bad updates, captured budgets, freshness/cadence and change evidence. Frozen archives stay frozen; daily metrics are not recalculated from ticks.
- Scoped the old selected-tab pseudo-element, which intercepted report export. The compiled full-flow export now passes. Do not bypass this with force-clicks or longer timeouts.
- Research browser checks respect each selected cutoff. Retained old data cannot satisfy a newer common close; the separately labelled latest-quotes view may show its dated technical evidence.

## Verification

- Full backend: **145 passed**. One existing Starlette/httpx dependency warning.
- Frontend: **31 passed**. Both final production builds passed (local and static project-subpath export).
- Pages browser: **14 passed**, including cooldown, hidden tabs, manifest-only checks, offline recovery and corrupt-update retention.
- Local browser: **14 passed**; research browser: **10 passed**. These validate retained real observations and isolated in-browser fixtures, not fresh provider access.
- Taste: two targeted rounds, final **zero FAIL / three WARN**. All six final desktop/tablet/phone fold and full-page captures were opened. Remaining warnings: `docs/freshness-validation.md`.
- Generated Pages capture: `e8200fa2-0726-48f8-bacd-4b99e106e491`, 21 October 13:25 UTC, all twelve sources unavailable, ledger accounting unknown. Disabled policies sent no provider requests. This is an internal validation artifact, not proof of a live feed or deployment.

Artifacts/logs remain ignored under `.runtime` and `.taste-skill/freshness-inspect-final`. Checkpoints preserve reviewable source and recovery instructions, not running processes or private runtime data after workspace loss.

## Remaining gates and exact next work

No faster schedule, crypto stream or production activation is enabled. All source policies are disabled. GitHub API/site and public providers last failed with proxy tunnel 403; native Git fetch works. Pages deployment, upstream access, quota provenance and hosting usage remain unverified. Do not repeat unsuccessful network probes without evidence of changed conditions.

The ledger is persistent local SQLite outside the disposable source cache. **Authoritative GitHub data-branch accounting, with reservations pushed before provider calls, is not implemented.** Never depend on `actions/cache` or recreate a missing ledger as a fresh budget. Activation remains closed until this and source verification are complete.

Next batch: design and test data-branch reservation persistence with local bare Git fixtures (failed push, competing workers, cancellation and restore). Then split quote/history refresh paths and add a data-only publisher. Keep its workflow disabled while external verification is blocked. Only a verified hourly pilot may precede faster US-session collection. The 80% soft reserve, multi-tab election and optional streaming remain future work.

Formatting and staged whitespace checks completed. Native Git push succeeded. The branch comparison is https://github.com/woussc/All-Seeing-Eye/compare/main...feat/freshness-controls . No PR or deployed revision is claimed. Main and the daily schedule are unchanged.

Resume from this branch with the local bare-Git reservation fixture design above. Verification commands: `services/market/.venv/bin/pytest -q services/market/tests`, `npm test`, `npm run build`, `npm run build:pages`, and the three `test:pages`, `test:browser`, `test:research-browser` scripts. Browser suites need the corresponding static/local preview running; do not rebuild local output while its Next production server is active. Repeat only checks affected by new edits.
