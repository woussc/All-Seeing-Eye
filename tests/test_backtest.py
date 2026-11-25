"""Tests for walk-forward backtest engine, post-March-2026 cutoff guard, costs, and BTC benchmark."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ase.backtest import (
    KnowledgeCutoffViolationError,
    WalkForwardBacktester,
    build_walk_forward_folds,
)
from ase.config import load_finalists, load_system_config
from ase.reflex import DeterministicRuleReflex
from ase.schemas import Candle


def _synthetic_series(
    start: datetime,
    n_bars: int,
    start_px: float,
    drift_per_bar: float,
) -> list[Candle]:
    bars: list[Candle] = []
    px = start_px
    for i in range(n_bars):
        open_ts = start + timedelta(hours=4 * i)
        close_ts = open_ts + timedelta(hours=4)
        nxt = px * (1.0 + drift_per_bar + (0.004 if i % 5 == 0 else -0.001))
        hi = max(px, nxt) * 1.006
        lo = min(px, nxt) * 0.994
        bars.append(
            Candle(
                open_ts=open_ts,
                close_ts=close_ts,
                open=px,
                high=hi,
                low=lo,
                close=nxt,
                volume=5000.0,
            )
        )
        px = nxt
    return bars


def test_walk_forward_folds_are_strictly_chronological_and_non_overlapping() -> None:
    folds = build_walk_forward_folds(n_bars=600, train_bars=180, test_bars=90)
    assert len(folds) >= 4
    prev_test_end = 0
    for fold in folds:
        assert fold.train_start < fold.train_end == fold.test_start < fold.test_end
        assert fold.test_start >= prev_test_end
        prev_test_end = fold.test_end


def test_model_backtest_enforces_post_march_2026_knowledge_cutoff() -> None:
    cfg = load_system_config("config/system.yaml")
    finalists = load_finalists("config/finalists.yaml")[:1]
    pre_cutoff = datetime(2026, 2, 1, 0, 0, tzinfo=timezone.utc)
    btc = _synthetic_series(pre_cutoff, 400, 70000.0, 0.0005)
    asset = _synthetic_series(pre_cutoff, 400, 100.0, 0.0008)

    bt = WalkForwardBacktester(cfg, finalists, reflex_provider=DeterministicRuleReflex())
    with pytest.raises(KnowledgeCutoffViolationError):
        bt.run(
            candles_by_symbol={"BTCUSDT": btc, finalists[0].symbol: asset},
            funding_by_symbol={},
            uses_llm_decisions=True,
        )


def test_walk_forward_applies_costs_and_compares_against_holding_btc() -> None:
    cfg = load_system_config("config/system.yaml")
    finalists = load_finalists("config/finalists.yaml")[:2]
    post_cutoff = datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)
    btc = _synthetic_series(post_cutoff, 480, 65000.0, 0.0015)  # strong BTC rally
    a1 = _synthetic_series(post_cutoff, 480, 50.0, 0.0040)      # strong trending finalist
    a2 = _synthetic_series(post_cutoff, 480, 120.0, -0.0002)

    bt = WalkForwardBacktester(cfg, finalists, reflex_provider=DeterministicRuleReflex())
    res = bt.run(
        candles_by_symbol={
            "BTCUSDT": btc,
            finalists[0].symbol: a1,
            finalists[1].symbol: a2,
        },
        funding_by_symbol={},
        uses_llm_decisions=True,
    )
    assert len(res.folds) >= 2
    assert res.btc_hold_return_pct > 0.0
    assert res.total_trades > 0
    assert res.total_fees_usd > 0.0
    assert res.total_slippage_usd > 0.0
    # When strategy lags BTC buy-and-hold, headline MUST state it FIRST
    summary = res.format_summary()
    if not res.beats_btc_after_costs:
        assert summary.startswith("WARNING: Strategy DID NOT beat holding BTC after costs")
