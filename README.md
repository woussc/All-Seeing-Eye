# SHUAI's Market Rotation Monitor

A research dashboard for SPY, QQQ, IWM, RSP, IEF, TLT, LQD, HYG, GLD, BTC, ETH and SGOV. It provides price context, completed-bar technical evidence, Treasury rates, source timestamps and downloadable reports.

**$0 design:** no market-data accounts, paid inference or broker connection. GitHub Pages serves the browser version; a public-repository GitHub Actions job collects public JSON/XML daily. The workflow refuses to run in private repositories. See [deployment and operation](docs/github-pages.md).

## GitHub Pages version

The five screens are Overview, Fund flows, Research, Journal and Sources. The website is static HTML, JavaScript and dated JSON, with no always-on Python server.

- Common close defaults to the last completed NYSE session, respecting holidays and early closes. Newer crypto observations use a separate clock.
- Technical evidence includes EMA slopes, confirmed pivots, four/thirteen-week returns and ATR/close. Stale or incomplete evidence keeps trends unclear.
- Refresh checks for a newer **published snapshot**. It cannot contact providers or trigger a GitHub job.
- The publisher runs around **08:17 Singapore time** daily. GitHub can delay or skip runs. Publications older than 36 hours are marked overdue and analytical watches are suppressed.
- Journal retains up to 30 captures while the publisher cache survives. Browser-local snapshots and JSON/Markdown downloads provide separate copies.
- Automatic measured fund flows remain unconnected. Flow uploads and server-side acknowledgments are available only in the local application.

Public Yahoo Finance, Coinbase Exchange and US Treasury observations can be delayed, unavailable or revised. Failed sources retain cached observations marked stale, or unavailable values without cache. Explicit `?demo=1` mode contains synthetic data and is never an outage fallback.

## Build the static site

Requirements: Node.js 24, Python 3.12 and uv. From the repository root:

```sh
npm ci --no-audit --no-fund
uv sync --project services/market --frozen
uv run --project services/market --frozen python services/market/export_pages.py
npm run build:pages
```

The result is `apps/pages/out`. Generated source at `apps/pages` excludes server API routes and is ignored by Git. `PAGES_BASE_PATH` defaults to `/All-Seeing-Eye`; use the repository path, or an empty string for an account-root site. `PAGES_DATA_DIR` can select another validated publication directory.

Only `apps/pages/out` is uploaded. The site excludes the local application's database, flow imports, credentials, conversation instructions and checkpoint notes.

## Local application

The original Next.js/FastAPI application remains available:

```sh
npm run build
bash scripts/start-local.sh
```

The launcher starts the web application on port 3000 and the loopback API on port 8000. Logs are in `.runtime`. Server-side archives, optional daily capture and validated flow imports are described in [research operations](docs/research-operations.md).

## Validation

```sh
npm test
npm run typecheck
uv run --project services/market --frozen pytest -q services/market/tests
npm run build:pages
npm run test:pages
```

The Pages browser check uses a temporary plain static HTTP server under the repository subpath, without an application backend. Set `CHROME_PATH` to a working Chromium executable outside the development workspace. The local application's browser scripts remain `npm run test:browser` and `npm run test:research-browser` and require running services.

See [data semantics](docs/data-contract.md), [technical definitions](docs/technical-evidence.md) and [Treasury sources](docs/macro-sources.md). This is a research monitor; no backtesting engine, trade execution, QuantGPT or Jev connection is included. DXY, HY spreads, positioning, release calendars and automatic fund flows remain unconnected.
