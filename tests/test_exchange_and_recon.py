"""Tests for exchange adapter (ccxt Binance testnet-first + paper fallback), fees, slippage, failed orders, and reconciliation."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ase.config import load_system_config
from ase.exchange import (
    CcxtBinanceAdapter,
    OrderExecutionError,
    OrderRequest,
    PaperExchangeAdapter,
    ReconciliationResult,
    create_exchange_adapter,
    reconcile_positions,
)


def test_paper_adapter_applies_fees_slippage_and_blocks_short_spot() -> None:
    cfg = load_system_config("config/system.yaml")
    adapter = PaperExchangeAdapter(initial_balance_usdt=10000.0, costs=cfg.costs)
    now = datetime(2026, 11, 24, 8, 0, tzinfo=timezone.utc)

    # Short on spot MUST fail
    with pytest.raises(OrderExecutionError):
        adapter.place_order(
            OrderRequest(
                symbol="AAVEUSDT",
                market_type="spot",
                side="sell",
                quantity=5.0,
                reference_price=100.0,
                ts=now,
            )
        )

    # Long spot applies slippage + spot taker fee (10 bps)
    fill = adapter.place_order(
        OrderRequest(
            symbol="AAVEUSDT",
            market_type="spot",
            side="buy",
            quantity=10.0,
            reference_price=100.0,
            ts=now,
        )
    )
    assert fill.status == "filled"
    assert fill.fill_price > 100.0  # adverse slippage
    assert fill.fee_usd > 0.0
    assert fill.slippage_usd > 0.0
    assert adapter.get_balance_usdt() < 10000.0

    # Short linear perp works cleanly
    short_fill = adapter.place_order(
        OrderRequest(
            symbol="ARBUSDT",
            market_type="linear_perps",
            side="sell",
            quantity=1000.0,
            reference_price=0.12,
            ts=now,
        )
    )
    assert short_fill.status == "filled"
    assert short_fill.fill_price < 0.12  # adverse slippage for short


def test_paper_adapter_handles_failed_orders_and_reconciliation_drift() -> None:
    cfg = load_system_config("config/system.yaml")
    adapter = PaperExchangeAdapter(initial_balance_usdt=10000.0, costs=cfg.costs, fail_next_order=True)
    now = datetime(2026, 11, 24, 8, 0, tzinfo=timezone.utc)

    with pytest.raises(OrderExecutionError):
        adapter.place_order(
            OrderRequest(
                symbol="AAVEUSDT",
                market_type="spot",
                side="buy",
                quantity=1.0,
                reference_price=100.0,
                ts=now,
            )
        )
    # Balance untouched on failed order
    assert adapter.get_balance_usdt() == pytest.approx(10000.0)

    # Place valid order then test reconciliation
    adapter.place_order(
        OrderRequest(
            symbol="AAVEUSDT",
            market_type="spot",
            side="buy",
            quantity=5.0,
            reference_price=100.0,
            ts=now,
        )
    )
    local_positions = {"AAVEUSDT": 5.0}
    ok_recon: ReconciliationResult = reconcile_positions(local_positions, adapter.fetch_positions())
    assert ok_recon.matched is True
    assert ok_recon.mismatches == {}

    # Introduce drift -> reconciliation MUST detect mismatch
    bad_recon = reconcile_positions({"AAVEUSDT": 7.5}, adapter.fetch_positions())
    assert bad_recon.matched is False
    assert "AAVEUSDT" in bad_recon.mismatches


def test_ccxt_adapter_falls_back_cleanly_to_paper_when_unreachable() -> None:
    cfg = load_system_config("config/system.yaml")
    testnet_cfg = cfg.model_copy(deep=True)
    testnet_cfg.system.mode = "testnet"

    def _unreachable_probe(_adapter: CcxtBinanceAdapter) -> None:
        raise ConnectionError("HTTP 451 geo-restricted from this host")

    adapter, info = create_exchange_adapter(testnet_cfg, connectivity_probe=_unreachable_probe)
    assert isinstance(adapter, PaperExchangeAdapter)
    assert info["fallback_used"] is True
    assert "451" in info["missing_piece"]
