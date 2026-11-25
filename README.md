# All-Seeing-Eye

24/7 two-layer (**BRAIN + REFLEX**) crypto trading system built around strict code-owned risk limits, causal state snapshots (`<400` tokens), `ccxt` Binance testnet-first execution (spot + linear USDT perpetuals, with paper fallback), post-March-2026 walk-forward validation, and nightly Brier-score calibration review.

> **Start in paper trading.** Live orders, moving funds, provisioning trading keys, and production deploys are blocked by an explicit code-level approval gate until owner sign-off.

## Core Documents (Read `AGENTS.md` and `PROGRESS.md` First Every Session)
- [Project Rules (`AGENTS.md`)](AGENTS.md)
- [Decision & Progress Log (`PROGRESS.md`)](PROGRESS.md)
- [Technical Specification (`docs/SPEC.md`)](docs/SPEC.md)
- [Test-First Implementation Plan (`docs/PLAN.md`)](docs/PLAN.md)
- [Dated Market Regime & 7-Finalist Research (`docs/RESEARCH_2026-11-24.md`)](docs/RESEARCH_2026-11-24.md)
- [24/7 Operations Runbook (`docs/RUNBOOK.md`)](docs/RUNBOOK.md)

## Quickstart
```bash
# Install dependencies (Python 3.12 + uv)
uv sync --dev

# Run full unit & integration test suite
uv run pytest -v

# Validate live approval gate (passes in paper mode; blocks unapproved live mode)
uv run ase validate-gate --config config/system.yaml

# Run post-March-2026 walk-forward backtest on official Binance archives
uv run ase backtest --config config/system.yaml --finalists config/finalists.yaml

# Run nightly Brier calibration review
uv run ase nightly --config config/system.yaml

# Launch 24/7 Docker Compose stack (paper mode default; survives reboots)
docker compose up -d --build
curl -fsS http://127.0.0.1:8080/healthz
```

## Non-Negotiable Code-Owned Risk Limits
- `1%` max risk per trade (inclusive of fees + modeled slippage)
- `5%` max daily loss (UTC)
- `3` max concurrent open positions
- `2x` max gross leverage (shorts routed via linear USDT perpetuals only)
- `15%` peak-to-trough drawdown permanent kill switch (manual reset only)
- Entry gates: `setup_quality >= 2`, `confidence > 0.80`, `risk_state == "safe"`, `regime != "crisis"`
- Pause new risk + Telegram alert on `confidence < 0.60` or `regime == "crisis"`
- Fixed minimum size for first `200` decisions, then `<= 0.25x` Kelly **only while** rolling Brier score beats baseline
- Hard `$5.00/day` UTC model budget cap; missed or over-budget candles are skipped cleanly
