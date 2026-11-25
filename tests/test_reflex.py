"""Tests for REFLEX schema validation, daily $ budget guard, missed-candle skips, and Gemini adapter."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from ase.reflex import (
    BudgetExceededError,
    DailyBudgetGuard,
    DeterministicRuleReflex,
    GeminiHydrogenReflexProvider,
    ReflexRunner,
)
from ase.schemas import MarketSnapshot, ReflexDecision


def _sample_snapshot() -> MarketSnapshot:
    return MarketSnapshot(
        symbol="AAVEUSDT",
        as_of_ts=datetime(2026, 11, 24, 8, 0, tzinfo=timezone.utc),
        last_close=95.0,
        ret_4h=0.012,
        ret_24h=0.035,
        ret_7d=-0.08,
        ema_slope_pct=0.006,
        btc_ret_7d=0.02,
        spread_bps=4.0,
        imbalance=0.35,
        realized_vol_20=0.022,
        funding_bps=1.0,
        gross_exposure_x=0.0,
        drawdown_pct=0.01,
        daily_pnl_pct=0.0,
        open_positions=0,
        token_count=120,
    )


def test_reflex_decision_schema_enforces_bounds() -> None:
    valid = ReflexDecision(
        regime="trending",
        direction="long",
        setup_quality=3,
        risk_state="safe",
        confidence=0.86,
        action="buy",
    )
    assert valid.setup_quality == 3

    with pytest.raises(ValidationError):
        ReflexDecision(
            regime="trending",
            direction="long",
            setup_quality=4,  # out of 0..3 range
            risk_state="safe",
            confidence=0.85,
            action="buy",
        )

    with pytest.raises(ValidationError):
        ReflexDecision(
            regime="unknown_regime",  # type: ignore[arg-type]
            direction="long",
            setup_quality=2,
            risk_state="safe",
            confidence=0.85,
            action="buy",
        )


def test_daily_budget_guard_blocks_over_limit_and_resets_next_utc_day() -> None:
    guard = DailyBudgetGuard(daily_limit_usd=5.0)
    day1 = datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc)
    day2 = datetime(2026, 11, 25, 0, 5, tzinfo=timezone.utc)

    guard.record_spend(3.5, ts=day1)
    assert guard.remaining_usd(day1) == pytest.approx(1.5)
    guard.check_can_spend(1.4, ts=day1)

    with pytest.raises(BudgetExceededError):
        guard.check_can_spend(1.6, ts=day1)

    # Next UTC day resets cleanly
    assert guard.remaining_usd(day2) == pytest.approx(5.0)
    guard.check_can_spend(4.9, ts=day2)


def test_reflex_runner_skips_candle_on_budget_exceeded_or_provider_error() -> None:
    snap = _sample_snapshot()

    # Over-budget skips cleanly instead of crashing or trading
    exhausted_budget = DailyBudgetGuard(daily_limit_usd=0.0001)
    exhausted_budget.record_spend(0.0001, ts=snap.as_of_ts)
    runner = ReflexRunner(provider=DeterministicRuleReflex(), budget=exhausted_budget)
    outcome = runner.evaluate(snap)
    assert outcome.skipped is True
    assert outcome.skip_reason == "budget_exceeded"
    assert outcome.decision is None

    # Failing provider skips cleanly
    class BrokenProvider:
        model_name = "broken"
        est_call_cost_usd = 0.001

        def decide(self, snapshot: MarketSnapshot) -> ReflexDecision:
            raise TimeoutError("simulated timeout")

    runner2 = ReflexRunner(provider=BrokenProvider(), budget=DailyBudgetGuard(daily_limit_usd=5.0))
    outcome2 = runner2.evaluate(snap)
    assert outcome2.skipped is True
    assert "timeout" in (outcome2.skip_reason or "").lower()
    assert outcome2.decision is None


def test_gemini_hydrogen_provider_builds_low_thinking_json_schema_request() -> None:
    captured: dict = {}

    class FakeResponse:
        text = (
            '{"regime":"trending","direction":"long","setup_quality":2,'
            '"risk_state":"safe","confidence":0.84,"action":"buy"}'
        )

    class FakeModels:
        def generate_content(self, **kwargs):
            captured.update(kwargs)
            return FakeResponse()

    class FakeClient:
        models = FakeModels()

    provider = GeminiHydrogenReflexProvider(
        model_name="gemini-4-hydrogen",
        thinking_level="low",
        client=FakeClient(),
    )
    decision = provider.decide(_sample_snapshot())
    assert decision.action == "buy"
    assert decision.setup_quality == 2
    assert captured["model"] == "gemini-4-hydrogen"
    cfg = captured["config"]
    assert cfg["response_mime_type"] == "application/json"
    assert cfg["response_schema"] is ReflexDecision
    assert cfg["thinking_config"]["thinking_level"] == "low"
