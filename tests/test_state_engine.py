"""Tests for causal state engine, numeric computations, feed/clock checks, and <400 token cap."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ase.schemas import Candle, OrderBookTop
from ase.state_engine import (
    CausalLeakageError,
    ClockSkewError,
    SnapshotTokenLimitError,
    StaleFeedError,
    StateEngine,
    estimate_tokens,
)


def _make_candles(n: int = 30, end_ts: datetime | None = None, base: float = 100.0) -> list[Candle]:
    end = end_ts or datetime(2026, 11, 24, 8, 0, tzinfo=timezone.utc)
    candles: list[Candle] = []
    for idx in range(n):
        close_ts = end - timedelta(hours=4 * (n - 1 - idx))
        open_ts = close_ts - timedelta(hours=4)
        px = base * (1.0 + 0.002 * idx)
        candles.append(
            Candle(
                open_ts=open_ts,
                close_ts=close_ts,
                open=px * 0.998,
                high=px * 1.01,
                low=px * 0.99,
                close=px,
                volume=1000.0 + idx * 10.0,
            )
        )
    return candles


def test_state_engine_builds_compact_numeric_snapshot_under_400_tokens() -> None:
    as_of = datetime(2026, 11, 24, 8, 0, tzinfo=timezone.utc)
    candles = _make_candles(30, end_ts=as_of, base=90.0)
    btc_candles = _make_candles(30, end_ts=as_of, base=60000.0)
    ob = OrderBookTop(
        ts=as_of - timedelta(seconds=10),
        bid=95.0,
        ask=95.095,
        bid_qty=400.0,
        ask_qty=100.0,
    )
    engine = StateEngine(max_tokens=400, stale_feed_max_age_sec=900, clock_skew_max_ms=5000)
    snap = engine.build_snapshot(
        symbol="AAVEUSDT",
        as_of_ts=as_of,
        local_now_ts=as_of + timedelta(seconds=2),
        exchange_server_ts=as_of + timedelta(seconds=1),
        candles=candles,
        btc_candles=btc_candles,
        orderbook=ob,
        funding_rate=0.0001,
        funding_ts=as_of - timedelta(hours=1),
        account_equity=9800.0,
        peak_equity=10000.0,
        day_start_equity=9900.0,
        gross_exposure_usd=4900.0,
        open_positions=1,
        daily_realized_pnl=-100.0,
    )

    payload = snap.to_reflex_payload()
    tok = estimate_tokens(payload)
    assert tok < 400, f"Snapshot payload must stay under 400 tokens, got {tok}"
    # Verify code-computed numeric metrics
    assert snap.spread_bps == pytest.approx((95.095 - 95.0) / 95.0475 * 10_000, rel=1e-3)
    assert snap.imbalance == pytest.approx((400.0 - 100.0) / 500.0, rel=1e-4)
    assert snap.realized_vol_20 > 0.0
    assert snap.gross_exposure_x == pytest.approx(0.5, rel=1e-4)
    assert snap.drawdown_pct == pytest.approx(0.02, rel=1e-4)
    assert snap.token_count == tok


def test_state_engine_blocks_future_causal_leakage() -> None:
    as_of = datetime(2026, 11, 24, 8, 0, tzinfo=timezone.utc)
    candles = _make_candles(25, end_ts=as_of + timedelta(hours=4))
    btc_candles = _make_candles(25, end_ts=as_of)
    ob = OrderBookTop(ts=as_of, bid=100.0, ask=100.1, bid_qty=10.0, ask_qty=10.0)
    engine = StateEngine()

    with pytest.raises(CausalLeakageError):
        engine.build_snapshot(
            symbol="AAVEUSDT",
            as_of_ts=as_of,
            local_now_ts=as_of,
            exchange_server_ts=as_of,
            candles=candles,
            btc_candles=btc_candles,
            orderbook=ob,
            funding_rate=0.0,
            funding_ts=as_of,
            account_equity=10000.0,
            peak_equity=10000.0,
            day_start_equity=10000.0,
            gross_exposure_usd=0.0,
            open_positions=0,
            daily_realized_pnl=0.0,
        )

    # Future funding timestamp also rejected
    good_candles = _make_candles(25, end_ts=as_of)
    with pytest.raises(CausalLeakageError):
        engine.build_snapshot(
            symbol="AAVEUSDT",
            as_of_ts=as_of,
            local_now_ts=as_of,
            exchange_server_ts=as_of,
            candles=good_candles,
            btc_candles=btc_candles,
            orderbook=ob,
            funding_rate=0.0005,
            funding_ts=as_of + timedelta(seconds=1),
            account_equity=10000.0,
            peak_equity=10000.0,
            day_start_equity=10000.0,
            gross_exposure_usd=0.0,
            open_positions=0,
            daily_realized_pnl=0.0,
        )


def test_state_engine_catches_stale_feeds_and_clock_skew() -> None:
    as_of = datetime(2026, 11, 24, 8, 0, tzinfo=timezone.utc)
    candles = _make_candles(25, end_ts=as_of)
    btc_candles = _make_candles(25, end_ts=as_of)
    stale_ob = OrderBookTop(
        ts=as_of - timedelta(seconds=1200),
        bid=100.0,
        ask=100.1,
        bid_qty=10.0,
        ask_qty=10.0,
    )
    engine = StateEngine(stale_feed_max_age_sec=900, clock_skew_max_ms=5000)

    with pytest.raises(StaleFeedError):
        engine.build_snapshot(
            symbol="AAVEUSDT",
            as_of_ts=as_of,
            local_now_ts=as_of,
            exchange_server_ts=as_of,
            candles=candles,
            btc_candles=btc_candles,
            orderbook=stale_ob,
            funding_rate=0.0,
            funding_ts=as_of,
            account_equity=10000.0,
            peak_equity=10000.0,
            day_start_equity=10000.0,
            gross_exposure_usd=0.0,
            open_positions=0,
            daily_realized_pnl=0.0,
        )

    fresh_ob = OrderBookTop(ts=as_of, bid=100.0, ask=100.1, bid_qty=10.0, ask_qty=10.0)
    with pytest.raises(ClockSkewError):
        engine.build_snapshot(
            symbol="AAVEUSDT",
            as_of_ts=as_of,
            local_now_ts=as_of,
            exchange_server_ts=as_of + timedelta(seconds=12),
            candles=candles,
            btc_candles=btc_candles,
            orderbook=fresh_ob,
            funding_rate=0.0,
            funding_ts=as_of,
            account_equity=10000.0,
            peak_equity=10000.0,
            day_start_equity=10000.0,
            gross_exposure_usd=0.0,
            open_positions=0,
            daily_realized_pnl=0.0,
        )


def test_state_engine_enforces_token_cap() -> None:
    as_of = datetime(2026, 11, 24, 8, 0, tzinfo=timezone.utc)
    candles = _make_candles(25, end_ts=as_of)
    btc_candles = _make_candles(25, end_ts=as_of)
    ob = OrderBookTop(ts=as_of, bid=100.0, ask=100.1, bid_qty=10.0, ask_qty=10.0)
    tiny_engine = StateEngine(max_tokens=10)
    with pytest.raises(SnapshotTokenLimitError):
        tiny_engine.build_snapshot(
            symbol="AAVEUSDT",
            as_of_ts=as_of,
            local_now_ts=as_of,
            exchange_server_ts=as_of,
            candles=candles,
            btc_candles=btc_candles,
            orderbook=ob,
            funding_rate=0.0,
            funding_ts=as_of,
            account_equity=10000.0,
            peak_equity=10000.0,
            day_start_equity=10000.0,
            gross_exposure_usd=0.0,
            open_positions=0,
            daily_realized_pnl=0.0,
        )
