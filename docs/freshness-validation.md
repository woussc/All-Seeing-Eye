# Freshness controls: validation record

21 October 2026, feature branch `feat/freshness-controls`.

The upgrade preserves the terminal layout and its Public Sans / IBM Plex Mono typography. It adds capture-time freshness and collection-budget/change evidence. No OpenStock source code or new account-backed dependency was copied into the application.

## Automated and browser evidence

Backend fixtures use temporary ledgers, mock transports and injected clocks. They cover concurrency, rolling windows, verification expiry, missing/corrupt state, failures, redirects, cooldowns, clock rollback and access-denial persistence across response order. No live traffic is used to discover a limit.

Client checks cover bounded responses, safe paths, contracts, hashes, mismatched files, CDN rollback, cooldown/backoff and overdue suppression. Production builds pass for both local mode and the GitHub Pages project subpath.

The 14 Pages checks exercise reports/downloads, local snapshots, responsive layouts, failed initial loads, hidden/offline tabs, manifest-only polling and corrupt updates. The 14 local and 10 research checks cover existing workflows and selected-cutoff evidence. Retained observations and browser-only fixtures are distinguished from fresh availability.

## Targeted visual review

Two Taste rounds were run at 1440px, 768px and 390px. All six final fold/full-page screenshots were opened. Final inspection: zero failures and three warnings; this is a targeted layout review, not an accessibility certification.

The new panel has aligned 20px padding and 13px primary labels. Provider cells wrap from six to four to two columns. Capture timestamps and unknown accounting remain explicit. The unavailable capture shows 0/12 observations and 12 needing attention, without placeholder prices. Browser checks found no horizontal page overflow.

Remaining warnings:

- Existing secondary metadata is often under 13px. New primary provider labels were raised to 13px; broader typography work is deferred.
- The twelve-source list is longer than the adjacent explanatory panel. Natural content heights remain; tablet/phone stack the panels.
- The existing phone Overview label sits 15px from the edge within its full-width button. No new clipping or overflow was observed.

Artifacts remain ignored in `.taste-skill/freshness-inspect-final` and `.runtime`. These results do not verify external API permission, free allowances, fresh data or a deployed Pages revision.
