"""Tests for OpenMarketAdapter (MCP JSON-RPC over HTTP, idempotency, tab/arm error handling, and 4h polling)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from ase.config import load_finalists, load_system_config
from ase.engine import TradingEngine
from ase.exchange import OrderExecutionError, OrderRequest
from ase.openmarket_adapter import OpenMarketAdapter, OpenMarketSymbolMap


def _mock_transport(responses: list[dict]) -> httpx.MockTransport:
    idx = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Authorization") == "Bearer test-om-token-123456"
        body = json.loads(request.content.decode("utf-8"))
        assert body["jsonrpc"] == "2.0"
        assert body["method"] == "tools/call"
        pos = idx["i"]
        idx["i"] += 1
        payload = responses[pos] if pos < len(responses) else responses[-1]
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": payload})

    return httpx.MockTransport(handler)


def test_openmarket_adapter_workspace_balance_positions_and_order_fill() -> None:
    responses = [
        # 1. chart_workspace_select
        {"structuredContent": {"status": "ok", "workspaceId": "2op55llf"}},
        # 2. account_balance
        {"structuredContent": {"data": {"equity": 10000.0, "available": 9500.0, "currency": "USDT"}}},
        # 3. account_positions
        {"structuredContent": {"data": {"positions": [{"symbol": "BINANCE_FUTURES:HYPEUSDT", "side": "long", "qty": 2.5}]}}},
        # 4. order_place -> filled
        {
            "structuredContent": {
                "data": {
                    "status": "filled",
                    "order_id": "om-ord-1",
                    "fill_price": 62.70,
                    "filled_qty": 2.0,
                    "fee": 0.0627,
                }
            }
        },
    ]
    client = httpx.Client(transport=_mock_transport(responses))
    adapter = OpenMarketAdapter(
        token="test-om-token-123456",
        workspace_id="2op55llf",
        http_client=client,
    )
    adapter.select_workspace()
    assert adapter.get_balance_usdt() == pytest.approx(10000.0)
    positions = adapter.fetch_positions()
    assert positions["HYPEUSDT"] == pytest.approx(2.5)

    fill = adapter.place_order(
        OrderRequest(
            symbol="HYPEUSDT",
            market_type="linear_perps",
            side="buy",
            quantity=2.0,
            reference_price=62.66,
            ts=datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc),
        )
    )
    assert fill.status == "filled"
    assert fill.order_id == "om-ord-1"
    assert fill.fill_price == pytest.approx(62.70)
    assert fill.fee_usd == pytest.approx(0.0627)


def test_openmarket_adapter_blocks_spot_short_and_surfaces_no_arm_or_rejected() -> None:
    responses = [
        {"structuredContent": {"data": {"status": "blocked", "reason": "no_arm"}}},
    ]
    client = httpx.Client(transport=_mock_transport(responses))
    adapter = OpenMarketAdapter(token="test-om-token-123456", workspace_id="2op55llf", http_client=client)

    now = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)
    # Spot short is prohibited in code before even calling OpenMarket
    with pytest.raises(OrderExecutionError, match="linear_perps"):
        adapter.place_order(
            OrderRequest(
                symbol="AAVEUSDT",
                market_type="spot",
                side="sell",
                quantity=1.0,
                reference_price=95.0,
                ts=now,
            )
        )

    # Tab not armed -> OrderExecutionError surfacing exact reason
    with pytest.raises(OrderExecutionError, match="no_arm"):
        adapter.place_order(
            OrderRequest(
                symbol="ARBUSDT",
                market_type="linear_perps",
                side="sell",
                quantity=100.0,
                reference_price=0.11,
                ts=now,
            )
        )


def test_openmarket_adapter_parses_completed_4h_candles_and_quotes() -> None:
    now = datetime(2026, 11, 24, 12, 5, tzinfo=timezone.utc)
    # 25 completed bars + 1 in-progress bar (open_ts=12:00 -> close_ts=16:00 > now)
    rows = []
    start = datetime(2026, 11, 20, 8, 0, tzinfo=timezone.utc)
    for i in range(26):
        t = start + timedelta(hours=4 * i)
        px = 60.0 + 0.1 * i
        rows.append([t.isoformat(), px, px + 0.5, px - 0.5, px + 0.2, 1000.0 + i])

    responses = [
        {
            "structuredContent": {
                "data": {
                    "series": [
                        {
                            "id": {"interval": "4h"},
                            "columns": ["t", "open", "high", "low", "close", "volume"],
                            "rows": rows,
                        }
                    ]
                }
            }
        },
        {
            "structuredContent": {
                "data": {
                    "bid": 62.50,
                    "ask": 62.55,
                    "bid_qty": 150.0,
                    "ask_qty": 90.0,
                    "ts": now.isoformat(),
                }
            }
        },
    ]
    client = httpx.Client(transport=_mock_transport(responses))
    adapter = OpenMarketAdapter(token="test-om-token-123456", workspace_id="2op55llf", http_client=client)

    candles = adapter.fetch_4h_candles("HYPEUSDT", "linear_perps", as_of_ts=now)
    # In-progress bar at 12:00 must be excluded (only completed bars close_ts <= now)
    assert len(candles) == 25
    assert all(c.close_ts <= now for c in candles)

    ob = adapter.fetch_orderbook_top("HYPEUSDT", "linear_perps", fallback_ts=now)
    assert ob.bid == pytest.approx(62.50)
    assert ob.ask == pytest.approx(62.55)


def test_trading_engine_poll_once_runs_cycle_with_openmarket_feed(tmp_path) -> None:
    cfg = load_system_config("config/system.yaml")
    cfg.system.db_path = str(tmp_path / "om_poll.db")
    finalists = load_finalists("config/finalists.yaml")[:1]
    now = datetime(2026, 11, 24, 12, 5, tzinfo=timezone.utc)

    # Build 25 completed 4h bars ending at 12:00 UTC
    start = datetime(2026, 11, 20, 8, 0, tzinfo=timezone.utc)
    rows = [
        [(start + timedelta(hours=4 * i)).isoformat(), 60.0 + i * 0.2, 61.0 + i * 0.2, 59.5 + i * 0.2, 60.5 + i * 0.2, 1000.0]
        for i in range(25)
    ]

    responses = [
        # BTCUSDT points (OHLCV)
        {"structuredContent": {"data": {"series": [{"id": {"interval": "FOUR_HOURS"}, "columns": ["t", "open", "high", "low", "close", "volume"], "rows": rows}]}}},
        # HYPEUSDT points (OHLCV)
        {"structuredContent": {"data": {"series": [{"id": {"interval": "FOUR_HOURS"}, "columns": ["t", "open", "high", "low", "close", "volume"], "rows": rows}]}}},
        # HYPEUSDT market_quote -> blocked (venue_off) -> falls back to causal bar proxy
        {"structuredContent": {"data": {"status": "blocked", "reason": "venue_off"}}},
        # HYPEUSDT funding (0.01% -> 0.0001 decimal)
        {"structuredContent": {"data": {"series": [{"columns": ["t", "rateClose"], "rows": [[(start + timedelta(hours=4 * 24)).isoformat(), 0.01]]}]}}},
    ]
    client = httpx.Client(transport=_mock_transport(responses))
    om = OpenMarketAdapter(token="test-om-token-123456", workspace_id="2op55llf", http_client=client)

    engine = TradingEngine(cfg, finalists)
    events = engine.poll_once(now=now, market_data_source=om)
    assert engine.last_cycle_ts is not None
    assert isinstance(events, list)
    assert OpenMarketSymbolMap.to_openmarket("HYPEUSDT", "linear_perps").endswith(":HYPEUSDT")
