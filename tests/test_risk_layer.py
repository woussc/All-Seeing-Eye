"""Tests for code-owned hard risk limits, sizing discipline, kill switch, and crisis/confidence pauses."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ase.config import load_system_config
from ase.risk import (
    HARD_KILL_SWITCH_DRAWDOWN_PCT,
    HARD_MAX_DAILY_LOSS_PCT,
    HARD_MAX_LEVERAGE,
    HARD_MAX_OPEN_POSITIONS,
    HARD_MAX_RISK_PER_TRADE_PCT,
    AccountState,
    RiskEngine,
)
from ase.schemas import ReflexDecision


def _good_decision(**overrides) -> ReflexDecision:
    base = dict(
        regime="trending",
        direction="long",
        setup_quality=3,
        risk_state="safe",
        confidence=0.88,
        action="buy",
    )
    base.update(overrides)
    return ReflexDecision(**base)


def test_config_cannot_loosen_hard_risk_caps() -> None:
    cfg = load_system_config("config/system.yaml")
    loose = cfg.risk.model_copy(
        update={
            "max_risk_per_trade_pct": 0.10,
            "max_daily_loss_pct": 0.25,
            "max_open_positions": 20,
            "max_leverage": 10.0,
            "kill_switch_drawdown_pct": 0.50,
            "min_setup_quality": 0,
            "min_confidence_to_trade": 0.50,
            "max_kelly_fraction": 1.0,
        }
    )
    engine = RiskEngine(loose)
    assert engine.max_risk_per_trade_pct == HARD_MAX_RISK_PER_TRADE_PCT
    assert engine.max_daily_loss_pct == HARD_MAX_DAILY_LOSS_PCT
    assert engine.max_open_positions == HARD_MAX_OPEN_POSITIONS
    assert engine.max_leverage == HARD_MAX_LEVERAGE
    assert engine.kill_switch_drawdown_pct == HARD_KILL_SWITCH_DRAWDOWN_PCT
    assert engine.min_setup_quality == 2
    assert engine.min_confidence_to_trade == 0.80
    assert engine.max_kelly_fraction == 0.25


def test_entry_gates_block_low_quality_low_confidence_or_unsafe_risk_state() -> None:
    cfg = load_system_config("config/system.yaml")
    engine = RiskEngine(cfg.risk)
    acct = AccountState(equity=10000.0, peak_equity=10000.0, day_start_equity=10000.0)

    # setup_quality < 2 rejected
    v = engine.evaluate(
        decision=_good_decision(setup_quality=1),
        account=acct,
        price=100.0,
        stop_loss_pct=0.05,
        fee_bps=5.0,
        slippage_bps=8.0,
        decision_count=10,
    )
    assert not v.approved and "setup_quality" in v.reason

    # confidence <= 0.80 rejected
    v = engine.evaluate(
        decision=_good_decision(confidence=0.80),
        account=acct,
        price=100.0,
        stop_loss_pct=0.05,
        fee_bps=5.0,
        slippage_bps=8.0,
        decision_count=10,
    )
    assert not v.approved and "confidence" in v.reason

    # risk_state != safe rejected
    v = engine.evaluate(
        decision=_good_decision(risk_state="near_limit"),
        account=acct,
        price=100.0,
        stop_loss_pct=0.05,
        fee_bps=5.0,
        slippage_bps=8.0,
        decision_count=10,
    )
    assert not v.approved and "risk_state" in v.reason


def test_pause_new_risk_and_alert_on_confidence_below_060_or_crisis_regime() -> None:
    cfg = load_system_config("config/system.yaml")
    engine = RiskEngine(cfg.risk)
    acct = AccountState(equity=10000.0, peak_equity=10000.0, day_start_equity=10000.0)

    v1 = engine.evaluate(
        decision=_good_decision(confidence=0.55),
        account=acct,
        price=100.0,
        stop_loss_pct=0.05,
        fee_bps=5.0,
        slippage_bps=8.0,
        decision_count=10,
    )
    assert not v1.approved
    assert v1.pause_new_risk is True
    assert v1.alert_telegram is True

    v2 = engine.evaluate(
        decision=_good_decision(regime="crisis", confidence=0.92),
        account=acct,
        price=100.0,
        stop_loss_pct=0.05,
        fee_bps=5.0,
        slippage_bps=8.0,
        decision_count=10,
    )
    assert not v2.approved
    assert v2.pause_new_risk is True
    assert v2.alert_telegram is True


def test_hard_limits_daily_loss_positions_leverage_and_permanent_kill_switch() -> None:
    cfg = load_system_config("config/system.yaml")
    engine = RiskEngine(cfg.risk)

    # 5% daily loss blocks new entries
    acct_day = AccountState(equity=9490.0, peak_equity=10000.0, day_start_equity=10000.0)
    v = engine.evaluate(_good_decision(), acct_day, 100.0, 0.05, 5.0, 8.0, decision_count=5)
    assert not v.approved and "daily_loss" in v.reason

    # 3 open positions blocks 4th
    acct_pos = AccountState(equity=10000.0, peak_equity=10000.0, day_start_equity=10000.0, open_positions=3)
    v = engine.evaluate(_good_decision(), acct_pos, 100.0, 0.05, 5.0, 8.0, decision_count=5)
    assert not v.approved and "max_open_positions" in v.reason

    # 2x leverage cap enforced even if tight stop would imply huge notional
    acct_lev = AccountState(equity=10000.0, peak_equity=10000.0, day_start_equity=10000.0, gross_exposure_usd=18000.0)
    v = engine.evaluate(_good_decision(), acct_lev, 100.0, 0.005, 5.0, 5.0, decision_count=250, rolling_brier=0.15, baseline_brier=0.25, win_rate=0.65, payoff_ratio=2.0)
    assert v.approved
    assert acct_lev.gross_exposure_usd + v.notional_usd <= 20000.0 + 1e-6

    # 15% drawdown permanently engages kill switch (no auto-reset even if equity bounces)
    acct_dd = AccountState(equity=8490.0, peak_equity=10000.0, day_start_equity=9000.0)
    v = engine.evaluate(_good_decision(), acct_dd, 100.0, 0.05, 5.0, 8.0, decision_count=5)
    assert not v.approved
    assert acct_dd.kill_switch_triggered is True
    assert v.alert_telegram is True
    # Subsequent bounce does NOT auto-reset kill switch
    acct_dd.equity = 9900.0
    v_after = engine.evaluate(_good_decision(), acct_dd, 100.0, 0.05, 5.0, 8.0, decision_count=6)
    assert not v_after.approved and "kill_switch" in v_after.reason


def test_fixed_min_first_200_decisions_then_quarter_kelly_only_if_brier_beats_baseline() -> None:
    cfg = load_system_config("config/system.yaml")
    engine = RiskEngine(cfg.risk)
    acct = AccountState(equity=10000.0, peak_equity=10000.0, day_start_equity=10000.0)

    # First 200 decisions: strictly fixed minimum size (5% notional = $500)
    v_early = engine.evaluate(
        _good_decision(),
        acct,
        price=100.0,
        stop_loss_pct=0.05,
        fee_bps=5.0,
        slippage_bps=8.0,
        decision_count=150,
        rolling_brier=0.10,
        baseline_brier=0.25,
        win_rate=0.65,
        payoff_ratio=2.0,
    )
    assert v_early.approved
    assert v_early.sizing_mode == "fixed_min"
    assert v_early.notional_usd == pytest.approx(500.0, rel=1e-3)

    # After 200 decisions, if Brier does NOT beat baseline -> stays fixed_min
    v_bad_brier = engine.evaluate(
        _good_decision(),
        acct,
        price=100.0,
        stop_loss_pct=0.05,
        fee_bps=5.0,
        slippage_bps=8.0,
        decision_count=220,
        rolling_brier=0.26,
        baseline_brier=0.25,
        win_rate=0.65,
        payoff_ratio=2.0,
    )
    assert v_bad_brier.approved
    assert v_bad_brier.sizing_mode == "fixed_min"
    assert v_bad_brier.notional_usd == pytest.approx(500.0, rel=1e-3)

    # After 200 decisions with Brier < baseline -> quarter Kelly bounded by 1% risk per trade
    v_kelly = engine.evaluate(
        _good_decision(),
        acct,
        price=100.0,
        stop_loss_pct=0.05,
        fee_bps=5.0,
        slippage_bps=8.0,
        decision_count=220,
        rolling_brier=0.14,
        baseline_brier=0.25,
        win_rate=0.65,
        payoff_ratio=2.0,
    )
    assert v_kelly.approved
    assert v_kelly.sizing_mode == "quarter_kelly"
    assert v_kelly.notional_usd > 500.0
    # Risk per trade MUST NOT exceed 1% ($100) inclusive of fees + slippage
    round_trip_cost_pct = 2 * (5.0 + 8.0) / 10_000.0
    worst_loss = v_kelly.notional_usd * (0.05 + round_trip_cost_pct)
    assert worst_loss <= 100.0 + 1e-6
