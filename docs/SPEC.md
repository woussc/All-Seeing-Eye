# All-Seeing-Eye — Technical Specification (`docs/SPEC.md`)

**Version:** 0.1.0 | **Date:** 2026-11-24 UTC | **Mode:** Paper-first (Live gated)

---

## 1. Architecture: Two Strictly Separated Layers

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          BRAIN LAYER (Offline/Daily)                    │
│  • Macro/regime research & fundamental screening (docs/RESEARCH_*.md)   │
│  • Compiled finalist universe (config/finalists.yaml)                   │
│  • Code, unit/integration tests, walk-forward validation                │
│  • Nightly self-improvement review & Brier calibration (ase.nightly)    │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │ Compiled schemas + immutable code rules
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          24/7 RUNTIME ENGINE                            │
│  1. Market Feeds (ccxt Binance testnet / Paper fallback)                │
│  2. State Engine (ase.state_engine)                                     │
│     • Strict causal timestamps (zero lookahead)                         │
│     • Code-computed spread, imbalance, volatility, exposure, drawdown   │
│     • Rejects stale feeds, exchange errors, clock skew (>5s)            │
│     • Emits single compact numeric snapshot (< 400 tokens)              │
│  3. REFLEX Layer (ase.reflex)                                           │
│     • Single ReflexProvider interface                                   │
│     • Gemini 4 Hydrogen, thinking_level="low", JSON schema output       │
│     • Hard $5.00/day USD budget guard; skips missed/over-budget candles │
│     • ONLY judges: regime, direction, setup_quality, risk_state, conf   │
│  4. Risk Layer (ase.risk) — 100% CODE-OWNED                             │
│     • Hard caps: 1% trade risk, 5% daily loss, 3 positions, 2x lev      │
│     • 15% drawdown permanent kill switch (manual reset only)            │
│     • Entry gate: quality>=2, conf>0.80, risk_state=='safe', !=crisis   │
│     • Sizing: fixed min first 200 decisions -> <=0.25 Kelly if Brier OK │
│     • Pause new risk + Telegram alert on conf<0.60 or regime=='crisis'  │
│  5. Execution & Reconciliation (ase.exchange)                           │
│     • ccxt Binance (spot long-only, linear USDT perps for shorts/longs) │
│     • Fees, slippage, failed orders, and position reconciliation        │
│  6. Persistence & Health (ase.storage, ase.engine)                      │
│     • SQLite WAL state + policy version history + rollback              │
│     • HTTP /healthz for Docker Compose health checks                    │
└─────────────────────────────────────────────────────────────────────────┘
```

## 2. REFLEX Output Schema (`src/ase/schemas.py`)
Every REFLEX evaluation MUST parse strictly into `ReflexDecision`:
- `regime`: `Literal["trending", "mean_reverting", "high_vol", "crisis"]`
- `direction`: `Literal["long", "short", "neutral"]`
- `setup_quality`: `int` constrained to `0 <= setup_quality <= 3`
- `risk_state`: `Literal["safe", "near_limit", "reduce"]`
- `confidence`: `float` constrained to `0.0 <= confidence <= 1.0`
- `action`: `Literal["buy", "sell", "hold"]` (validated against `direction`: `buy -> long`, `sell -> short`, `hold -> neutral` or non-actionable)

## 3. State Engine Contract (`src/ase/state_engine.py`)
- Input: completed 4h candle OHLCV history (strictly `close_ts <= as_of_ts`), top-of-book bid/ask + depth, perp funding rate (strictly `calc_ts <= as_of_ts`), account equity/positions/drawdown, exchange server timestamp vs local UTC clock.
- Invariants:
  - Raises `CausalLeakageError` if any candle or funding timestamp exceeds `as_of_ts`.
  - Raises `StaleFeedError` if latest feed observation exceeds configured max age.
  - Raises `ClockSkewError` if `abs(exchange_ts_ms - local_ts_ms) > clock_skew_max_ms`.
  - Computes `spread_bps`, `orderbook_imbalance`, `realized_vol_20`, `gross_exposure_x`, `drawdown_pct`, `daily_pnl_pct`, `ema_slope_pct`, `ret_4h`, `ret_24h`, `ret_7d`, `btc_ret_7d`, `funding_bps` strictly in Python.
  - Serializes to compact numeric JSON/KV payload verified strictly `< 400` tokens (`ceil(len(chars) / 3.5)` conservative bound AND whitespace/punct token counter).

## 4. Code-Owned Hard Risk Limits (`src/ase/risk.py`)
Immutable module-level constants define the absolute ceiling; YAML config may tighten but never loosen them:
- `HARD_MAX_RISK_PER_TRADE_PCT = 0.01`
- `HARD_MAX_DAILY_LOSS_PCT = 0.05`
- `HARD_MAX_OPEN_POSITIONS = 3`
- `HARD_MAX_LEVERAGE = 2.0`
- `HARD_KILL_SWITCH_DRAWDOWN_PCT = 0.15`
- `HARD_MIN_SETUP_QUALITY = 2`
- `HARD_MIN_CONFIDENCE_TO_TRADE = 0.80`
- `HARD_PAUSE_CONFIDENCE_THRESHOLD = 0.60`
- `HARD_FIXED_MIN_DECISIONS = 200`
- `HARD_MAX_KELLY_FRACTION = 0.25`

Sizing math:
- First 200 decisions: notional = `min(equity * fixed_min_notional_pct, risk_capped_notional, leverage_capped_notional)`.
- Decision > 200: compute empirical Kelly fraction `f* = max(0, p - (1 - p) / b)` capped at `0.25 * f*` ONLY IF rolling `brier_score < baseline_brier_score`; otherwise remain at fixed minimum size.
- Stop-loss risk cap ALWAYS bounds position size: `qty * (stop_distance + 2 * slippage + 2 * fee) <= equity * 0.01`.

## 5. Walk-Forward Backtesting Contract (`src/ase/backtest.py`)
- Strictly expanding or rolling walk-forward folds; no full-sample peeking.
- Mandatory friction: taker fees (spot 10 bps / perp 5 bps per side), half-spread + slippage (BTC/ETH 1+5 bps; alts 3+8 bps per side), and observed Binance perpetual funding rates at scheduled settlement hours.
- **Knowledge cutoff guard:** When `uses_llm_decisions=True`, raises `KnowledgeCutoffViolationError` if `start_ts < 2026-04-01T00:00:00Z` (strictly after Gemini 4 Neon/Hydrogen March 2026 cutoff).
- **BTC benchmark:** Every report computes net OOS return, Sharpe, and max drawdown vs. holding BTC spot (with entry/exit fee+slippage) over the identical OOS timestamps and prints a prominent warning FIRST if the strategy underperforms holding BTC.

## 6. Nightly Self-Improvement Loop (`src/ase/nightly.py`)
- Reviews every logged fill, exit, skipped candle, and missed trade since last review.
- Computes rolling and window **Brier score** `mean((predicted_prob - realized_outcome)^2)` vs. simple climatology baseline `p_bar * (1 - p_bar)` (and 0.25 reference).
- Proposes **at most ONE** candidate policy mutation (e.g., confidence threshold nudge, stop width adjustment, or finalist weight) stored as a new versioned `PolicyVersion` with `status="proposed"`.
- Requires explicit pass of both `walk_forward_passed` and `paper_gate_passed` flags before promotion to `paper_active`.
- Retains full previous version snapshot for one-command rollback (`ase rollback <version>`).
- `auto_push_live` is hardcoded `False`: a policy can NEVER be silently pushed live.

## 7. Security & Approval Gate
- Secret files MUST reside outside the git repository root (`SecretGuard` verifies resolved path is not under repo root).
- Global `SecretRedactingFilter` strips any registered secret value from logs, exceptions, and Telegram alerts.
- Live mode (`mode: live`) is blocked unless `ASE_LIVE_APPROVED=true`, a non-empty `ASE_LIVE_APPROVAL_TOKEN` is supplied outside the repo, and `exchange.testnet_first` has been explicitly overridden by the owner.
