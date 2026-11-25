"""Exchange execution layer: ccxt Binance (testnet first, spot + linear perps), paper fallback, and reconciliation."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal, Protocol

from ase.config import CostsSection, SystemConfig
from ase.schemas import MarketType


class OrderExecutionError(RuntimeError):
    """Raised when an order is rejected, invalid, or fails on the venue."""


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    market_type: MarketType
    side: Literal["buy", "sell"]
    quantity: float
    reference_price: float
    ts: datetime
    reduce_only: bool = False


@dataclass(frozen=True)
class OrderFill:
    order_id: str
    symbol: str
    market_type: MarketType
    side: Literal["buy", "sell"]
    quantity: float
    fill_price: float
    fee_usd: float
    slippage_usd: float
    status: Literal["filled", "rejected"]
    ts: datetime


@dataclass(frozen=True)
class ReconciliationResult:
    matched: bool
    mismatches: dict[str, tuple[float, float]] = field(default_factory=dict)


class ExchangeAdapter(Protocol):
    mode: str

    def get_balance_usdt(self) -> float: ...

    def place_order(self, req: OrderRequest) -> OrderFill: ...

    def fetch_positions(self) -> dict[str, float]: ...

    def server_time_utc(self) -> datetime: ...


class PaperExchangeAdapter:
    """Realistic local paper adapter modeling fees, spread/slippage, spot/perp rules, and failed orders."""

    mode = "paper"

    def __init__(
        self,
        *,
        initial_balance_usdt: float = 10_000.0,
        costs: CostsSection | None = None,
        fail_next_order: bool = False,
    ) -> None:
        self.cash_usdt = float(initial_balance_usdt)
        self.costs = costs or CostsSection()
        self.fail_next_order = fail_next_order
        self._positions: dict[str, float] = {}
        self._entry_prices: dict[str, float] = {}
        self.fills: list[OrderFill] = []

    def get_balance_usdt(self) -> float:
        return self.cash_usdt

    def fetch_positions(self) -> dict[str, float]:
        return {sym: qty for sym, qty in self._positions.items() if abs(qty) > 1e-12}

    def server_time_utc(self) -> datetime:
        return datetime.now(timezone.utc)

    def place_order(self, req: OrderRequest) -> OrderFill:
        if self.fail_next_order:
            self.fail_next_order = False
            raise OrderExecutionError(f"Simulated exchange failure for {req.symbol} {req.side}")

        if req.quantity <= 0 or req.reference_price <= 0:
            raise OrderExecutionError("Order quantity and reference price must be positive")

        current_qty = self._positions.get(req.symbol, 0.0)
        signed_delta = req.quantity if req.side == "buy" else -req.quantity

        # Hard invariant: shorting is ONLY allowed via linear perpetuals, never spot
        if req.market_type == "spot" and current_qty + signed_delta < -1e-12:
            raise OrderExecutionError(
                f"Short spot prohibited for {req.symbol}: use linear_perps for short positions"
            )

        fee_bps, slip_bps = self.costs.side_fee_and_slippage_bps(req.symbol, req.market_type)
        side_sign = 1.0 if req.side == "buy" else -1.0
        fill_price = req.reference_price * (1.0 + side_sign * slip_bps / 10_000.0)
        notional = req.quantity * fill_price
        fee_usd = notional * (fee_bps / 10_000.0)
        slippage_usd = req.quantity * req.reference_price * (slip_bps / 10_000.0)

        # Realize PnL if reducing an existing position
        if current_qty != 0.0 and (current_qty * signed_delta) < 0:
            close_qty = min(abs(current_qty), req.quantity)
            entry_px = self._entry_prices.get(req.symbol, req.reference_price)
            pos_sign = 1.0 if current_qty > 0 else -1.0
            realized = pos_sign * close_qty * (fill_price - entry_px)
            self.cash_usdt += realized

        new_qty = current_qty + signed_delta
        if abs(new_qty) <= 1e-12:
            self._positions.pop(req.symbol, None)
            self._entry_prices.pop(req.symbol, None)
        else:
            if current_qty == 0.0 or (current_qty * signed_delta) > 0:
                total_abs = abs(current_qty) + req.quantity
                prev_px = self._entry_prices.get(req.symbol, fill_price)
                self._entry_prices[req.symbol] = (
                    (abs(current_qty) * prev_px + req.quantity * fill_price) / total_abs
                )
            self._positions[req.symbol] = new_qty

        self.cash_usdt -= fee_usd
        fill = OrderFill(
            order_id=f"paper-{uuid.uuid4().hex[:12]}",
            symbol=req.symbol,
            market_type=req.market_type,
            side=req.side,
            quantity=req.quantity,
            fill_price=fill_price,
            fee_usd=fee_usd,
            slippage_usd=slippage_usd,
            status="filled",
            ts=req.ts,
        )
        self.fills.append(fill)
        return fill


class CcxtBinanceAdapter:
    """ccxt Binance adapter supporting testnet-first spot and USDT-M linear perpetuals."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        testnet: bool = True,
        costs: CostsSection | None = None,
    ) -> None:
        import ccxt

        self.testnet = testnet
        self.mode = "testnet" if testnet else "live"
        self.costs = costs or CostsSection()

        common = {"enableRateLimit": True, "timeout": 10000}
        if api_key and api_secret:
            common["apiKey"] = api_key
            common["secret"] = api_secret

        self.spot = ccxt.binance({**common, "options": {"defaultType": "spot"}})
        self.perp = ccxt.binanceusdm({**common, "options": {"defaultType": "future"}})
        if testnet:
            self.spot.set_sandbox_mode(True)
            self.perp.set_sandbox_mode(True)

    @staticmethod
    def _to_ccxt_symbol(symbol: str, market_type: MarketType) -> str:
        s = symbol.upper().replace("/", "").replace(":USDT", "")
        base = s[:-4] if s.endswith("USDT") else s
        return f"{base}/USDT" if market_type == "spot" else f"{base}/USDT:USDT"

    def probe_connectivity(self) -> None:
        self.spot.fetch_time()
        self.perp.fetch_time()

    def server_time_utc(self) -> datetime:
        ms = self.spot.fetch_time()
        return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)

    def get_balance_usdt(self) -> float:
        spot_bal = self.spot.fetch_balance().get("total", {}).get("USDT", 0.0) or 0.0
        perp_bal = self.perp.fetch_balance().get("total", {}).get("USDT", 0.0) or 0.0
        return float(spot_bal) + float(perp_bal)

    def fetch_positions(self) -> dict[str, float]:
        positions: dict[str, float] = {}
        for pos in self.perp.fetch_positions():
            contracts = float(pos.get("contracts") or 0.0)
            if abs(contracts) > 1e-12:
                side = pos.get("side")
                sym = str(pos.get("symbol", "")).replace("/USDT:USDT", "USDT")
                positions[sym] = contracts if side == "long" else -contracts
        return positions

    def place_order(self, req: OrderRequest) -> OrderFill:
        if req.market_type == "spot" and req.side == "sell" and not req.reduce_only:
            raise OrderExecutionError("Short spot orders are prohibited; route shorts to linear_perps")
        client = self.spot if req.market_type == "spot" else self.perp
        ccxt_sym = self._to_ccxt_symbol(req.symbol, req.market_type)
        params = {"reduceOnly": True} if (req.market_type == "linear_perps" and req.reduce_only) else {}
        try:
            order = client.create_market_order(ccxt_sym, req.side, req.quantity, params=params)
        except Exception as exc:
            raise OrderExecutionError(f"ccxt order failed for {req.symbol}: {exc}") from exc

        fill_px = float(order.get("average") or order.get("price") or req.reference_price)
        filled_qty = float(order.get("filled") or req.quantity)
        fee_info = order.get("fee") or {}
        fee_usd = float(fee_info.get("cost") or 0.0)
        slippage_usd = abs(fill_px - req.reference_price) * filled_qty
        return OrderFill(
            order_id=str(order.get("id", "")),
            symbol=req.symbol,
            market_type=req.market_type,
            side=req.side,
            quantity=filled_qty,
            fill_price=fill_px,
            fee_usd=fee_usd,
            slippage_usd=slippage_usd,
            status="filled",
            ts=datetime.now(timezone.utc),
        )


def reconcile_positions(
    local_positions: dict[str, float],
    exchange_positions: dict[str, float],
    *,
    tolerance: float = 1e-6,
) -> ReconciliationResult:
    """Compare local ledger positions against venue positions and flag any drift."""
    mismatches: dict[str, tuple[float, float]] = {}
    all_syms = set(local_positions) | set(exchange_positions)
    for sym in sorted(all_syms):
        loc = float(local_positions.get(sym, 0.0))
        exc = float(exchange_positions.get(sym, 0.0))
        if abs(loc - exc) > tolerance:
            mismatches[sym] = (loc, exc)
    return ReconciliationResult(matched=len(mismatches) == 0, mismatches=mismatches)


def create_exchange_adapter(
    cfg: SystemConfig,
    *,
    api_key: str | None = None,
    api_secret: str | None = None,
    connectivity_probe: Callable[[CcxtBinanceAdapter], None] | None = None,
) -> tuple[ExchangeAdapter, dict[str, Any]]:
    """Create Binance ccxt adapter (testnet-first) or clean PaperExchangeAdapter fallback."""
    log = logging.getLogger("ase.exchange")
    if cfg.system.mode == "paper":
        return (
            PaperExchangeAdapter(
                initial_balance_usdt=cfg.account.initial_paper_balance_usdt,
                costs=cfg.costs,
            ),
            {"mode": "paper", "fallback_used": False, "missing_piece": None},
        )

    testnet = cfg.system.mode == "testnet" or cfg.exchange.testnet_first
    adapter = CcxtBinanceAdapter(
        api_key=api_key,
        api_secret=api_secret,
        testnet=testnet,
        costs=cfg.costs,
    )
    try:
        if connectivity_probe is not None:
            connectivity_probe(adapter)
        else:
            adapter.probe_connectivity()
        return adapter, {"mode": adapter.mode, "fallback_used": False, "missing_piece": None}
    except Exception as exc:
        if not cfg.exchange.fallback_to_paper_if_unreachable:
            raise
        missing = f"Binance {adapter.mode} unreachable ({exc}); requires eligible host/IP and testnet API keys"
        log.warning("Falling back to PaperExchangeAdapter: %s", missing)
        return (
            PaperExchangeAdapter(
                initial_balance_usdt=cfg.account.initial_paper_balance_usdt,
                costs=cfg.costs,
            ),
            {"mode": "paper_fallback", "fallback_used": True, "missing_piece": missing},
        )
