# All-Seeing-Eye — Test-First Build Plan (`docs/PLAN.md`)

**Date:** 2026-11-24 UTC

## Phase 1: Specification & Configuration (Complete)
- [x] Record owner-confirmed parameters (`Binance`, spot+perps, `$10,000` paper, off-limits filter, `4h`, `$5/day` LLM cap).
- [x] Author `AGENTS.md`, `PROGRESS.md`, `docs/SPEC.md`, `docs/RESEARCH_2026-11-24.md`, `docs/RUNBOOK.md`.
- [x] Compile `config/system.yaml` and `config/finalists.yaml`.

## Phase 2: Test-First Specification Suite (`tests/`)
Write failing unit & integration tests BEFORE writing production modules:
1. `tests/test_config_and_secrets.py`:
   - Rejects memecoins, leveraged tokens, `<$200M` mcap, `<$20M` 24h volume.
   - Rejects secret paths located inside the repository tree.
   - Redacts API keys and Telegram bot tokens from all log output.
   - Blocks live execution when approval gate is not explicitly satisfied.
2. `tests/test_state_engine.py`:
   - Rejects future candle/funding timestamps (`CausalLeakageError`).
   - Rejects stale feeds (`StaleFeedError`) and excessive clock skew (`ClockSkewError`).
   - Computes spread, order book imbalance, realized volatility, exposure, and drawdown in code accurately.
   - Enforces compact numeric snapshot strictly `< 400` tokens.
3. `tests/test_reflex.py`:
   - Validates `ReflexDecision` JSON schema bounds (`setup_quality` 0–3, `confidence` 0–1, enum constraints).
   - Verifies `DailyBudgetGuard` blocks calls exceeding `$5.00/day` and skips the candle cleanly.
   - Verifies missed/timeout/malformed REFLEX responses skip the candle without trading.
   - Verifies `GeminiHydrogenReflexProvider` constructs `gemini-4-hydrogen` request with `thinking_level="low"` and JSON schema without making real network calls in unit tests.
4. `tests/test_risk_layer.py`:
   - Enforces hard caps: `1%` trade risk, `5%` daily loss, `3` max positions, `2x` leverage cap, `15%` drawdown permanent kill switch (no auto-reset).
   - Blocks trades if `setup_quality < 2`, `confidence <= 0.80`, `risk_state != "safe"`, or `regime == "crisis"`.
   - Verifies fixed minimum size for first 200 decisions, then `<= 0.25` Kelly ONLY while Brier score beats baseline.
   - Verifies pause of new risk + Telegram alert trigger on `confidence < 0.60` or `regime == "crisis"`.
   - Verifies config cannot relax hard caps.
5. `tests/test_exchange_and_recon.py`:
   - Verifies spot long-only enforcement (shorts routed to linear USDT perps only).
   - Verifies fees, slippage, failed order handling, and position reconciliation drift detection.
   - Verifies `CcxtBinanceAdapter` testnet-first initialization and clean fallback to `PaperExchangeAdapter` on network/451 error.
6. `tests/test_backtest.py`:
   - Verifies walk-forward splits have zero lookahead overlap.
   - Verifies fees, slippage, and perpetual funding reduce net returns vs gross.
   - Verifies `KnowledgeCutoffViolationError` for model decisions on pre-April-2026 data.
   - Verifies BTC buy-and-hold comparator reports underperformance FIRST when strategy trails BTC.
7. `tests/test_nightly_and_engine.py`:
   - Verifies Brier score calculation on resolved decisions.
   - Verifies nightly review proposes at most 1 change, blocks promotion until walk-forward + paper gates pass, supports version rollback, and never auto-pushes live.
   - Verifies HTTP `/healthz` endpoint returns healthy JSON with mode, uptime, kill-switch status, and budget spend.

## Phase 3: Production Implementation (`src/ase/`)
Implement modules cleanly until `uv run pytest -q` passes 100%:
- `schemas.py`, `config.py`, `alerts.py`, `storage.py`, `state_engine.py`, `reflex.py`, `risk.py`, `exchange.py`, `data_loader.py`, `backtest.py`, `nightly.py`, `engine.py`, `cli.py`.

## Phase 4: Real Post-March-2026 Walk-Forward Backtest & Paper Runtime Verification
- Download official Binance `data.binance.vision` 4h spot, USDT-M perpetual klines, and funding rates for `2026-04-01` through `2026-11-23` (strictly post-March-2026 cutoff) for `BTCUSDT` + 7 finalists, verifying SHA-256 checksums.
- Run walk-forward backtest with fees, slippage, and funding ON; report exact results vs holding BTC.
- Run paper trading engine end-to-end smoke check (with live `/healthz` HTTP probe, paper order fill, reconciliation, and nightly Brier review + rollback drill).
- Ship `Dockerfile`, `docker-compose.yml`, `.github/workflows/ci.yml`, and `README.md`.
