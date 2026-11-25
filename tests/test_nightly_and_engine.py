"""Tests for nightly calibration (Brier score), 1-change versioned policy + rollback, and /healthz server."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from ase.config import load_finalists, load_system_config
from ase.engine import HealthServer
from ase.nightly import NightlyReviewer, compute_brier_score
from ase.storage import StateStore


def test_brier_score_computation() -> None:
    # Perfect calibration -> 0.0
    assert compute_brier_score([(1.0, 1), (0.0, 0)]) == pytest.approx(0.0)
    # Constant 0.5 -> 0.25
    assert compute_brier_score([(0.5, 1), (0.5, 0)]) == pytest.approx(0.25)
    # Overconfident wrong -> 1.0
    assert compute_brier_score([(1.0, 0), (0.0, 1)]) == pytest.approx(1.0)


def test_nightly_review_proposes_one_change_gates_promotion_and_supports_rollback(tmp_path: Path) -> None:
    db_path = tmp_path / "ase.db"
    store = StateStore(db_path)
    store.initialize()

    # Seed initial active policy v1
    v1 = store.save_policy_version(
        version="v1",
        params={"min_confidence_to_trade": 0.80, "stop_loss_multiplier": 1.0},
        status="paper_active",
        rationale="Initial baseline policy",
    )
    assert v1.version == "v1"

    # Log resolved decisions with poor calibration (overconfident misses -> Brier > 0.25)
    now = datetime(2026, 11, 24, 0, 15, tzinfo=timezone.utc)
    for i in range(10):
        store.log_decision(
            ts=now,
            symbol="AAVEUSDT",
            regime="high_vol",
            direction="long",
            setup_quality=2,
            risk_state="safe",
            confidence=0.90,
            action="buy",
            executed=True,
            skip_reason=None,
            realized_outcome=1 if i < 3 else 0,  # 30% win rate at 0.90 conf -> bad Brier
            pnl_usd=40.0 if i < 3 else -50.0,
        )
    # Log one missed/skipped candle
    store.log_decision(
        ts=now,
        symbol="UNIUSDT",
        regime="high_vol",
        direction="neutral",
        setup_quality=0,
        risk_state="safe",
        confidence=0.50,
        action="hold",
        executed=False,
        skip_reason="timeout",
        realized_outcome=None,
        pnl_usd=0.0,
    )

    reviewer = NightlyReviewer(store)
    report = reviewer.run_review(as_of_ts=now)
    assert report.fills_reviewed == 10
    assert report.misses_reviewed == 1
    assert report.brier_score > 0.25
    assert report.proposed_version is not None
    assert report.proposed_version.status == "proposed"
    assert len(report.proposed_changes) == 1  # STRICTLY one change proposed
    assert report.auto_pushed_live is False

    # Cannot promote without BOTH walk_forward_passed and paper_gate_passed
    with pytest.raises(ValueError):
        reviewer.promote_proposal(
            report.proposed_version.version,
            walk_forward_passed=True,
            paper_gate_passed=False,
        )

    # Passes both gates -> promotes to paper_active (still NEVER live)
    promoted = reviewer.promote_proposal(
        report.proposed_version.version,
        walk_forward_passed=True,
        paper_gate_passed=True,
    )
    assert promoted.status == "paper_active"
    assert store.get_active_policy().version == promoted.version

    # Rollback returns cleanly to v1
    rolled = reviewer.rollback("v1")
    assert rolled.version == "v1"
    assert store.get_active_policy().version == "v1"


def test_health_server_responds_on_healthz() -> None:
    status_state = {
        "status": "ok",
        "mode": "paper",
        "exchange_mode": "paper_fallback",
        "kill_switch_triggered": False,
        "daily_budget_spent_usd": 0.12,
    }
    server = HealthServer(host="127.0.0.1", port=0, status_fn=lambda: status_state)
    server.start()
    try:
        resp = httpx.get(f"http://127.0.0.1:{server.port}/healthz", timeout=3.0)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["mode"] == "paper"
        assert body["kill_switch_triggered"] is False
    finally:
        server.stop()


def test_trading_engine_end_to_end_paper_cycle_and_reconciliation_pause(tmp_path: Path) -> None:
    from datetime import timedelta

    from ase.alerts import TelegramAlerter
    from ase.engine import TradingEngine
    from ase.schemas import Candle, MarketSnapshot, OrderBookTop, ReflexDecision

    cfg = load_system_config("config/system.yaml")
    cfg.system.db_path = str(tmp_path / "engine.db")
    finalists = load_finalists("config/finalists.yaml")[:1]

    class AlwaysBuyReflex:
        model_name = "test-buy-reflex"
        est_call_cost_usd = 0.01

        def decide(self, snapshot: MarketSnapshot) -> ReflexDecision:
            return ReflexDecision(
                regime="trending",
                direction="long",
                setup_quality=3,
                risk_state="safe",
                confidence=0.90,
                action="buy",
            )

    alerter = TelegramAlerter()
    engine = TradingEngine(
        cfg,
        finalists,
        reflex_provider=AlwaysBuyReflex(),
        alerter=alerter,
    )

    as_of = datetime(2026, 11, 24, 8, 0, tzinfo=timezone.utc)
    sym = finalists[0].symbol

    def _bars(base: float) -> list[Candle]:
        out: list[Candle] = []
        for idx in range(30):
            close_ts = as_of - timedelta(hours=4 * (29 - idx))
            open_ts = close_ts - timedelta(hours=4)
            px = base * (1.0 + 0.002 * idx)
            out.append(
                Candle(
                    open_ts=open_ts,
                    close_ts=close_ts,
                    open=px * 0.998,
                    high=px * 1.01,
                    low=px * 0.99,
                    close=px,
                    volume=1000.0,
                )
            )
        return out

    events = engine.run_candle_cycle(
        as_of_ts=as_of,
        candles_by_symbol={"BTCUSDT": _bars(60000.0), sym: _bars(60.0)},
        orderbooks_by_symbol={
            sym: OrderBookTop(ts=as_of, bid=63.4, ask=63.45, bid_qty=200.0, ask_qty=100.0)
        },
        funding_by_symbol={sym: (0.0001, as_of)},
    )
    assert len(events) == 1
    assert events[0]["event"] == "filled"
    assert engine.account.open_positions == 1
    assert engine.health_payload()["status"] == "ok"

    # Inject reconciliation drift -> engine MUST pause new risk and alert Telegram
    engine.local_positions[sym] += 5.0
    drift_events = engine.run_candle_cycle(
        as_of_ts=as_of + timedelta(hours=4),
        candles_by_symbol={"BTCUSDT": _bars(60000.0), sym: _bars(60.0)},
        orderbooks_by_symbol={
            sym: OrderBookTop(ts=as_of, bid=63.4, ask=63.45, bid_qty=200.0, ask_qty=100.0)
        },
        funding_by_symbol={sym: (0.0001, as_of)},
    )
    assert drift_events[0]["event"] == "reconciliation_mismatch"
    assert engine.account.new_risk_paused is True
    assert any("reconciliation mismatch" in m.lower() for m in alerter.sent_messages)

