# Research storage and operations

The local API stores flow revisions, alert states/events and archived reports in the same SQLite database as the price cache. The default path is `services/market/data/market.sqlite3`; `MARKET_DB_PATH` overrides it. This is a single-workspace pilot, not a hosted multi-user service.

## Import published weekly flows

Use the Fund flows screen to download the JSON template, fill it from a dataset you are permitted to use, then choose the file. The service validates a preview before the user commits. Canceling the preview writes no observations. The template contains blank required fields and cannot be imported unchanged.

Each series has a stable ID, label, scope, category, source name/URL, rights note and expected constituents. The supported categories are named ETFs, US Bitcoin ETF baskets, US Ethereum ETF baskets, global gold ETF baskets and published fund categories. Keep overlapping scopes separate: there is no sum across categories.

Each record represents exactly seven consecutive publisher-defined calendar dates. Supply period start/end, the actual publication timestamp with timezone, signed net flow in USD, complete/partial status, covered constituents and a positive revision number. A complete period requires every expected constituent and a flow value; zero is a valid value. The importer does not establish publisher accuracy or entitlement from a rights note.

The 1/4/13-week views end on the newest imported period. They require consecutive periods and unchanged basket membership. Missing weeks or partial baskets withhold the entire total. Data older than fourteen calendar days is marked stale. Optional normalization divides total flow by the first period's beginning AUM, dated the day before that period starts, using the same basket. The raw dollar flow remains available when the denominator is absent.

Existing revisions cannot be overwritten. A corrected observation needs a higher revision and a non-earlier publication timestamp. Scope/source/label metadata changes require a new series ID; constituent changes withhold cross-period comparisons. Reimporting the same payload is idempotent. Batches are atomic, including preview rollback. Limits are 500 records per import, 100 series, 900 KB through the UI/CLI, and 1 MB through the API. All revisions remain on disk; response payloads read only the latest thirteen-week range per series.

CLI preview and commit, with an existing database:

```bash
cd /workspace/All-Seeing-Eye
services/market/.venv/bin/python services/market/maintenance.py import-flows --file /path/to/permitted-export.json
services/market/.venv/bin/python services/market/maintenance.py import-flows --file /path/to/permitted-export.json --commit
```

No real flow dataset has been imported during development. Unit and browser fixtures remain isolated from the running store.

## Reports and watch states

“Save snapshot” retains up to twelve entries in the current browser. “Archive source snapshot,” in the Journal, stores an immutable server report containing the complete source snapshot, a SHA-256 digest, deterministic briefing, gaps and a link to the preceding entry. Only a source snapshot in the server's short-term cache can be archived; the client cannot supply an arbitrary replacement dataset. Expired cache entries require a refresh before archival. Reports survive cache eviction and process/browser restarts, but loss of the environment still requires a backup.

The watch panel evaluates source health and selective equity broadening. The analytical watch uses adjusted history for exactly five completed NYSE sessions ending at the current cutoff. Entry is RSP minus SPY above 0.25 percentage points with IWM minus SPY at or below zero; the active state persists above 0.15 points to reduce boundary flicker. These are provisional research thresholds. Invalid/stale/missing inputs suppress the analytical watch. Acknowledging a state does not resolve it; a state transition resets acknowledgment. Events persist locally and no external notification is sent.

Reports describe the captured evidence, not what was known before collection. Publication vintages and historical revisions are incomplete. No report is a bias-free backtest or a validated trading signal.

New archives also contain `briefing.monitorMarkdown`, exported through “Export monitor report.” It uses the completed-session cutoff for the twelve-reference table and keeps newer crypto quotes separate. It shows basis-point yield changes, scoped flow evidence, evidence gaps, three research priorities, a prior captured-state reference and the required disabled Jev status. The original JSON export remains available with all source metadata. Older immutable reports do not acquire this new field retroactively. The browser-hosted version uses the separate read-only publication process described in [GitHub Pages operation](github-pages.md).

## Optional daily capture

The local daily worker is off by default. To enable it for the running application:

```bash
MARKET_CAPTURE_DAILY=1 bash scripts/start-local.sh
```

While that process runs, it checks once per minute and stores at most one daily report per Singapore calendar day at or after 08:00. A late startup captures current evidence with the actual capture time. It does not reconstruct 08:00 data, backfill missed days, change the existing ChatGPT task, or deliver messages. `GET /health` exposes whether the worker is enabled and its latest error. Manual reports and daily reports coexist.

## Backup and recovery

Use unique output paths; commands refuse to overwrite files. The online SQLite backup API includes committed WAL data and runs `PRAGMA integrity_check`. It can back up a running instance.

```bash
cd /workspace/All-Seeing-Eye
services/market/.venv/bin/python services/market/maintenance.py backup --output /workspace/backups/rotation-YYYYMMDD.sqlite3
services/market/.venv/bin/python services/market/maintenance.py restore --input /workspace/backups/rotation-YYYYMMDD.sqlite3 --output /workspace/recovered/rotation.sqlite3
services/market/.venv/bin/python services/market/maintenance.py --database /workspace/recovered/rotation.sqlite3 verify-report REPORT_ID
```

Stop the application's own launcher before switching databases. Restart with `MARKET_DB_PATH=/workspace/recovered/rotation.sqlite3 bash scripts/start-local.sh`; verify health and a saved report. The recovery command creates a new database and leaves the current one intact. Keep an additional backup outside this environment for protection from environment loss. No external backup destination or automatic backup schedule is configured.

## Deployment gate

The backend binds to loopback. The web proxy restricts routes, request sizes and mutation origins; those controls are not user authentication. Local browser writes work on loopback origins. For an authenticated private preview behind a TLS proxy, set `DASHBOARD_ORIGIN` to the exact browser origin (for example `https://your-private-preview.example`, without a trailing slash) before starting Next.js. Forwarded-host headers are not trusted to grant write access. Keep this pilot behind the workspace's access boundary. External hosting still needs authentication, TLS, an explicit application origin, persistent storage/backups, provider entitlements and a reviewed deployment destination. No public deployment or paid source subscription has been created.
