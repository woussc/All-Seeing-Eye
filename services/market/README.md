# Market research API

FastAPI service for the twelve-asset dashboard. `GET /snapshot` implements `../../contracts/market.ts`; `GET /health` validates service/storage availability. A healthy service can have unavailable providers. `mode: connected` means adapters were requested, never a guarantee that feeds succeeded.

## Reproduce locally

Python 3.12+, `uv`, no PostgreSQL required for this development slice:

```bash
cd /workspace/All-Seeing-Eye/services/market
UV_CACHE_DIR=/workspace/.cache/uv uv sync --frozen
.venv/bin/pytest -q
.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8000
```

In another terminal, request `http://127.0.0.1:8000/health` and `/snapshot` using a local HTTP client. The Next.js application fetches through its own server; this service does not expose vendor credentials or enable browser CORS. No API keys are required for these public research adapters. Optional `MARKET_DB_PATH` points to a writable SQLite file (default `services/market/data/market.sqlite3`). TLS certificate verification remains enabled. Dependencies are locked in `uv.lock` and installed into the service-local `.venv`.

## Verified runtime

The production service and 122 backend tests pass. At 23:12 UTC on 19 October 2026, a full snapshot returned all twelve price observations and four Treasury series with delayed status. The Treasury values matched official CSV downloads. Source availability may change independently of service health.

The service also supports sourced weekly-flow imports, persistent watch states, immutable reports and optional daily capture. See [research operations](../../docs/research-operations.md) and [macro definitions](../../docs/macro-sources.md). No real flow dataset has been imported.

## Sources and rights

| Adapter                                | Data requested                                                                                                              | Important limits                                                                                                                                                                                                                                                                                                                                   |
| -------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Yahoo Finance public chart, unofficial | SPY, QQQ, IWM, RSP, IEF, TLT, LQD, HYG, GLD, SGOV; five years of daily OHLCV/adjusted close plus regular-session last quote | Unofficial, undocumented and unstable interface; no guaranteed delay, availability or entitlement. Research-only integration, never represented as a licensed production real-time feed. Review [Yahoo terms](https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html) and obtain an entitled replacement before production/redistribution.      |
| Coinbase Exchange public market API    | BTC-USD/ETH-USD ticker, rolling 24h stats, provider-returned recent daily candles, without historical pagination            | One exchange, USD pairs; not a consolidated market/index. Candle history can contain gaps. Public access is not a redistribution license. Review [market-data API docs](https://docs.cdp.coinbase.com/exchange/reference/exchangerestapi_getproductcandles) and applicable [Coinbase terms](https://www.coinbase.com/legal) before production use. |

Requests are bounded by a 5-second HTTP phase timeout and 6-second total symbol refresh timeout. Concurrent quote requests are serialized at the snapshot-refresh layer to prevent duplicate fetches. ETF successes and failures cache for 300 seconds; crypto successes and failures for 30 seconds. This is deliberately a modest development polling cadence. There are no rate-limit evasion retries. Provider failures preserve a visibly **stale** last-good record; a source with no prior valid record is **unavailable**. Data is marked **delayed** even when its timestamp is current, since this public research integration makes no licensed real-time claim.

Observation age in seconds is exposed in each populated asset's note. ETF staleness compares the observation against the actual most recent NYSE close (20 minute tolerance), or against the current clock during an open session after its first 20 minutes. Crypto quotes older than two minutes are stale. Calendar calculations use `exchange_calendars` XNYS, including DST, exchange holidays and early closes. If the calendar cannot establish a cutoff, `commonCloseAt` is null.

## Time and calculation definitions

- All timestamps are explicit ISO8601 UTC. Naive observation timestamps, observations more than five seconds in the future (clock-skew tolerance), and nonfinite prices are rejected. Response floats retain provider precision; display rounding belongs to the frontend.
- ETF `price` is Yahoo's regular-session last trade, with its own `regularMarketTime`; extended-hours quotes are excluded. Latest 1D and 1W changes compare that raw price with raw closes from the preceding one and five exchange sessions. These are **price returns**, not total returns: splits and distributions can distort them. SGOV and bond-fund cash distributions must not be interpreted as fund outflows or rotation alerts.
- ETF history uses verified completed daily observations only, with provider-adjusted close/OHLC for indicators. The `time` label is the calendar-defined close of that daily aggregate's session; it does not assert that a trade occurred at that exact clock instant. Trend classification must compare the latest adjusted `history` value with the adjusted EMAs, rather than mixing in a raw live quote. Incomplete daily rows are excluded; rows lacking adjustments cannot supply adjusted indicators.
- Coinbase `price` and `asOf` come from the same ticker response. 1D compares this ticker price with the public stats window's opening price; those requests are concurrent, not atomic. 1W is null because no exact seven-day comparison trade is requested. Completed UTC candles supply history, time-labelled at the end of their daily aggregation interval. Crypto is 24/7; it does not share the ETF session-return convention.
- `commonCloseAt` is the latest completed NYSE cutoff strictly before request generation. ETF `commonClose` uses the verified completed regular-session daily **raw** closing bar, labelled with that session's calendar cutoff as its aggregation endpoint. It is explicitly an aggregate close, not an assertion of an exact trade timestamp. Its 1D/1W returns compare raw closing bars from the preceding one/five exchange sessions. A last trade at 15:59:59 does not invalidate an otherwise completed daily close. Crypto requires a genuine mark at the same exact cutoff; this adapter does not store/fetch such historical marks, so crypto `commonClose` remains null.
- Indicators use only a contiguous suffix of completed adjusted ETF bars or unadjusted crypto daily bars. A missing expected session/day resets warmup. EMA uses an initial period-length SMA seed, then `alpha=2/(period+1)`, and publishes after **five periods** of observations: 100/250/1000 bars for EMA20/50/200. Consequently the observed 349-candle crypto history cannot publish EMA200 (no deeper backfill is implemented).
- RSI14 uses Wilder gain/loss means (flat series = 50); ATR14 uses Wilder-smoothed true range; ADX14 uses Wilder directional movement and DX. All require at least **150 contiguous bars** to reduce initialization effects. Relative volume is latest completed volume / mean of the preceding 20 completed volumes; only crypto supports it because split-adjusted ETF volume is not established.
- Volatility is sample standard deviation of the latest 20 daily log returns, multiplied by `sqrt(252)` for ETFs or `sqrt(365)` for crypto and by 100 for annualized percent units. Returns use percent units: 1.25 means 1.25%, not 125%. Insufficient history produces null, never zero or a guessed indicator.

## Persistence and remaining scope

SQLite `observations` retains source URL, observation time, publication time when available (**null for the price endpoints**), ingestion time and the last-good parsed payload. `attempts` tracks refresh failures separately. The latest 100 snapshot documents form a short-term cache. Full immutable reports, flow revisions and alert events live in separate tables and survive cache eviction. Data files and virtual environments are gitignored.

Use `maintenance.py` for online backup, restoration to a new path, flow import and report verification. Recovery of an actual archived report and its digest has been checked. The optional daily-capture loop is off by default and requires a running service; it does not provide an always-on hosting guarantee.

PostgreSQL remains the deployment target. Production still needs entitled sources, persistent hosting, external backup storage, authentication and deployment configuration. Public data does not expose auditable historical availability vintages, and adjusted historical bars may be revised. This implementation cannot reconstruct an information cutoff or prove a bias-free backtest. Credit spreads, DXY, positioning, event calendars, external alert delivery, model-written explanations and trade execution remain outside the implemented service.

Completed-bar trend-v1 evidence is now returned in optional `Asset.technicals`. It adds 4/13-week adjusted/venue returns, five-bar EMA slopes, ATR14/close, confirmed pivots and conservative classifications. See [technical evidence](../../docs/technical-evidence.md) for the exact rules and remaining limits. This calculation uses retained verified history even during an outage; source staleness remains explicit and suppresses current trend confirmation.
