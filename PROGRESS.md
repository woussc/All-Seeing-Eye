# All-Seeing-Eye — Decision & Progress Log (`PROGRESS.md`)

**Read `AGENTS.md` and this file first in every session. Record every material decision with date and rationale.**

---

## 2026-11-24 — Initial System Overhaul & Architecture Decisions

### Decision 001: Full Overhaul of `All-Seeing-Eye` into 24/7 Two-Layer Trading System
- **Context:** Repository previously housed a static Next.js / FastAPI ETF Market Rotation Monitor (`$0` GitHub Pages dashboard) with no execution, risk, or backtesting engine. User authorized full overhaul of `All-Seeing-Eye` using `shuai-capital` as reference.
- **Decision:** Replace legacy Next.js/Pages monitor with a clean Python 3.12 package (`src/ase/`), Docker Compose 24/7 runtime, strict test-first pytest suite, and dated research/runbook documentation.
- **Reason:** Eliminates dead code and enforces the "no loose scripts: spec, plan, test-first build, review, ship with config, runbook, and approval gate" mandate.

### Decision 002: Owner-Confirmed Trading Parameters
- **Exchange:** Binance via `ccxt` (testnet first, with `PaperExchangeAdapter` fallback).
- **Markets:** Spot (long-only) + Linear USDT Perpetuals (shorts via perps, matching `shuai-capital` convention).
- **Starting Paper Balance:** `$10,000` USDT (matching `shuai-capital/playbook.md`).
- **Off-Limits Universe:** Memecoins, leveraged tokens, and illiquid coins (`market_cap < $200M` or `24h_volume < $20M`).
- **Primary Candle Timeframe:** `4h` (optimizes signal-to-noise vs. 1h/15m fees and stays well within `$5/day` REFLEX model budget).
- **Daily Model Call Budget:** `$5.00/day` hard cap enforced in code.

### Decision 003: Risk Cap Alignment (5% Daily Loss Cap vs. `shuai-capital` 3%)
- **Context:** `shuai-capital/playbook.md` used a 3% daily loss limit; the system specification explicitly sets hard caps of `1%` risk/trade, `5%` max daily loss, `3` open positions, `2x` leverage, and `15%` drawdown kill switch.
- **Decision:** Enforce the specification's hard ceilings (`1%` trade risk, `5%` max daily loss, `3` max positions, `2x` max leverage, `15%` drawdown kill switch) as immutable code caps in `src/ase/risk.py`. Operators may configure tighter limits via `config/system.yaml`, never looser ones.
- **Reason:** Guarantees code-owned hard limits that neither config drift nor LLM outputs can bypass.

### Decision 004: Exchange Geo-Restriction Discovery & Paper Adapter Fallback
- **Observation (2026-11-24 11:33 UTC):** Probing `https://api.binance.com/api/v3/ping` and `https://testnet.binance.vision/api/v3/ping` from this VM returned `HTTP/2 451` (CloudFront geo-restriction). However, `https://data.binance.vision/` (official SHA-256 checksummed Binance historical spot/perp/funding archive), `https://api.llama.fi/` (DefiLlama), and `https://api.coingecko.com/` all returned `HTTP/2 200`.
- **Decision:** Implement full `CcxtBinanceAdapter` (testnet-first, spot + USDT-M perps) AND automatic/explicit `PaperExchangeAdapter` fallback with realistic fees, slippage, failed-order simulation, and reconciliation. Use official `data.binance.vision` archives (April 1 – November 23, 2026) for post-cutoff walk-forward backtests and replay paper runs.
- **Reason:** Never fabricate exchange connectivity; deliver a working, testable ccxt adapter ready for an eligible non-US server while keeping full paper testing and post-cutoff walk-forward verification operational right now.

### Decision 005: REFLEX Model Interface & Post-March-2026 Cutoff Guard
- **Decision:** Build a single `ReflexProvider` interface supporting `GeminiHydrogenReflexProvider` (calling `gemini-4-hydrogen` via `google-genai` with `thinking_level="low"` and strict `response_schema=ReflexDecision`) and `DeterministicRuleReflex` (zero-API-cost causal baseline for offline unit tests and walk-forward backtests when no Gemini API key is mounted).
- **Reason:** Enforces clean separation of BRAIN and REFLEX, prevents unverified/fake model calls without credentials, and enforces a strict `data_start >= 2026-04-01 UTC` guard whenever model-driven backtests run (post March 2026 knowledge cutoff).

### Decision 006: Initial Finalist Universe Selection (7 Liquid Setups, 2026-11-24)
- **Decision:** Select 7 fundamental-disconnect finalists passing all off-limits filters (`mcap >= $200M`, `24h_vol >= $20M`, non-meme, non-leveraged, all verified available on Binance spot + USDT-M perp):
  - **Value-Accrual Long Candidates:** `HYPE` (Hyperliquid), `AAVE` (Aave), `UNI` (Uniswap), `SKY` (Sky Protocol — conditional mean-reversion gated on USDS stabilization).
  - **Tactical / Catalyst-Gated Neutral-to-Short:** `ENA` (Ethena — USDe growing, but negative perp funding collapsed 24h fees to `$21k` while fee switch requires `$7.5B` USDe).
  - **Asymmetric Unlock / Weak-Accrual Short Candidates:** `ARB` (Arbitrum), `SUI` (Sui).
  - **Excluded by Off-Limits Rule:** `AERO` (`$100M` mcap < `$200M` threshold).
- **Reason:** Anchored to dated primary sources (DefiLlama API, CoinGecko API, Tokenomist, governance disclosures as of 2026-11-24 UTC) and aligned with the prevailing risk-off/liquidity-contraction regime (`-$17.4B` 30d stablecoin supply, negative BTC/ETH perp funding, hawkish Fed/strong DXY).

### Decision 007: Verification Results & Explicit Caveats (2026-11-24 11:50 UTC)
- **Unit & Integration Tests:** `28 passed` (`uv run pytest -v`), covering off-limits filtering, external secret location check, key redaction, live approval gate, strict causality, `<400`-token snapshots, stale feed/clock skew rejection, `$5/day` budget cap, hard risk limits, 200-decision fixed-min / quarter-Kelly + Brier gating, spot-short prohibition, failed orders, position reconciliation, walk-forward knowledge cutoff guard, nightly single-change proposal/rollback, and live `/healthz` HTTP probe.
- **Docker Compose & Gate Validation:** `docker compose config -q` passed; `ase validate-gate` passed in `paper` mode and blocked unapproved `live` mode.
- **Post-March-2026 Walk-Forward Simulation (Official SHA-256-Verified Binance 4h Spot + USDT-M Perp + Funding Archives, OOS `2026-05-01` to `2026-11-12` UTC, 13 folds, `DeterministicRuleReflex` baseline):**
  - **1x Modeled Costs:** Strategy `+8.92%` (`$10,000 -> $10,892.28`, Sharpe `1.55`, Max DD `4.97%`, `308` trades, `47.1%` win rate, fees `$201.03`, slippage `$359.92`, funding `-$6.70`, Brier `0.3790`, `KillSwitch: False`) vs. **BTC Hold after costs** `-3.36%` (Sharpe `-0.03`, Max DD `29.25%`).
  - **2x Fees & Slippage Stress Test:** Strategy `+7.52%` (`$10,000 -> $10,751.59`, Sharpe `1.30`, Max DD `5.33%`, `306` trades, fees `$404.41`, slippage `$627.70`, funding `-$7.22`, Brier `0.3814`) vs. **BTC Hold after costs** `-3.65%`.
- **Critical Scientific Caveats (Never Hidden):**
  1. **Finalist Selection Hindsight Bias:** Finalists and `allowed_directions` were chosen on `2026-11-24`, *after* the May–Nov 2026 OOS window. This inflates backtest returns; true prospective OOS validation begins forward from `2026-11-24` in paper trading.
  2. **Brier Calibration Failure (`0.3790 > 0.25`):** Raw confidence (`0.82–0.85`) is overconfident relative to `47.1%` hit rate; `RiskEngine` properly blocked quarter-Kelly sizing and stayed at fixed minimum size.
  3. **Blocked Live Integrations:** Binance API/testnet returned `HTTP 451` from this VM IP; no `GEMINI_API_KEY`, Binance testnet keys, or Telegram bot token are provisioned yet. All live/testnet/Gemini API calls remain unexecuted until provisioned by the owner.

### Decision 008: OpenMarket MCP HTTP Adapter (`OpenMarketAdapter`, Option A)
- **Context:** Antigravity UI does not expose custom remote MCP URL registration in this session; owner requested connecting paper trading to OpenMarket workspace `2op55llf`.
- **Decision:** Implemented `src/ase/openmarket_adapter.py` (`OpenMarketAdapter`) calling `https://mcp.openmarket.xyz/` over Streamable HTTP JSON-RPC 2.0 using an external Agent Token (`/etc/ase-secrets/openmarket_token`, never inside the repo), and wired automatic 4h completed-bar polling (`TradingEngine.poll_once` + `serve_forever`).
- **Reason:** Enables direct paper trading and live chart data on workspace `2op55llf` with idempotent order keys and full code-owned `RiskEngine` enforcement without relying on a chat-level MCP connector.

