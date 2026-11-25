"""OpenMarket MCP Streamable-HTTP adapter for chart workspace 2op55llf (Option A).

Connects directly from Python using an external Agent Token (stored outside the repo,
e.g. /etc/ase-secrets/openmarket_token) without requiring an in-chat MCP connector.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from ase.exchange import OrderExecutionError, OrderFill, OrderRequest
from ase.schemas import Candle, MarketType, OrderBookTop

DEFAULT_OPENMARKET_MCP_URL = "https://mcp.openmarket.xyz/"


class OpenMarketSymbolMap:
    """Maps All-Seeing-Eye symbols to OpenMarket EXCHANGE:SYMBOL format."""

    @staticmethod
    def to_openmarket(symbol: str, market_type: MarketType) -> str:
        clean = symbol.upper().replace("/", "").replace(":USDT", "")
        if market_type == "spot":
            return f"BINANCE:{clean}"
        return f"BINANCE_FUTURES:{clean}"

    @staticmethod
    def from_openmarket(raw: str) -> str:
        sym = raw.split(":")[-1].upper().replace("/", "")
        return sym


class OpenMarketAdapter:
    """Calls OpenMarket MCP tools over HTTP JSON-RPC 2.0 with idempotency & tab-arm guards."""

    mode = "openmarket_paper"

    def __init__(
        self,
        *,
        token: str,
        workspace_id: str = "2op55llf",
        mcp_url: str = DEFAULT_OPENMARKET_MCP_URL,
        http_client: httpx.Client | None = None,
        timeout_sec: float = 15.0,
    ) -> None:
        if not token or not token.strip():
            raise ValueError("OpenMarket Agent Token is required (store outside repo in /etc/ase-secrets/)")
        self._token = token.strip()
        self.workspace_id = workspace_id
        self.mcp_url = mcp_url
        self.timeout_sec = timeout_sec
        self._rpc_id = 0
        self._log = logging.getLogger("ase.openmarket")
        self._client = http_client or httpx.Client(timeout=timeout_sec)

    def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self._rpc_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._rpc_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        resp = self._client.post(self.mcp_url, json=payload, headers=headers, timeout=self.timeout_sec)
        resp.raise_for_status()
        body = resp.json()
        if "error" in body and body["error"]:
            raise RuntimeError(f"OpenMarket MCP error on {name}: {body['error']}")
        result = body.get("result", body)
        structured = result.get("structuredContent", result)
        return structured.get("data", structured)

    def select_workspace(self) -> dict[str, Any]:
        return self._call_tool("chart_workspace_select", {"workspaceId": self.workspace_id})

    def probe_connectivity(self) -> None:
        self.select_workspace()
        self._call_tool("chart_status", {})

    def server_time_utc(self) -> datetime:
        return datetime.now(timezone.utc)

    def get_balance_usdt(self) -> float:
        data = self._call_tool("account_balance", {})
        for key in ("equity", "total", "balance", "available", "cash"):
            if key in data and data[key] is not None:
                return float(data[key])
        raise RuntimeError(f"Unexpected account_balance payload from OpenMarket: {data}")

    def fetch_positions(self) -> dict[str, float]:
        data = self._call_tool("account_positions", {})
        raw_positions = data.get("positions", data if isinstance(data, list) else [])
        positions: dict[str, float] = {}
        for pos in raw_positions:
            qty = float(pos.get("qty") or pos.get("quantity") or pos.get("size") or 0.0)
            if abs(qty) <= 1e-12:
                continue
            sym = OpenMarketSymbolMap.from_openmarket(str(pos.get("symbol", "")))
            side = str(pos.get("side", "long")).lower()
            positions[sym] = abs(qty) if side == "long" else -abs(qty)
        return positions

    def fetch_4h_candles(
        self,
        symbol: str,
        market_type: MarketType,
        *,
        as_of_ts: datetime | None = None,
        limit: int = 45,
    ) -> list[Candle]:
        cutoff = (as_of_ts or datetime.now(timezone.utc)).astimezone(timezone.utc)
        om_sym = OpenMarketSymbolMap.to_openmarket(symbol, market_type)
        data = self._call_tool(
            "points",
            {"symbol": om_sym, "interval": "4h", "limit": limit},
        )
        series = data.get("series", [])
        if not series:
            raise RuntimeError(f"No 4h candle series returned by OpenMarket for {om_sym}")
        item = series[0]
        cols = item.get("columns", [])
        rows = item.get("rows", [])
        col_idx = {c: i for i, c in enumerate(cols)}
        candles: list[Candle] = []
        for r in rows:
            raw_t = r[col_idx["t"]]
            if isinstance(raw_t, (int, float)):
                open_ts = datetime.fromtimestamp(
                    raw_t / 1000.0 if raw_t > 10**11 else raw_t, tz=timezone.utc
                )
            else:
                open_ts = datetime.fromisoformat(str(raw_t).replace("Z", "+00:00")).astimezone(timezone.utc)
            close_ts = open_ts + timedelta(hours=4)
            # Strictly causal: exclude any in-progress candle whose close_ts > cutoff
            if close_ts > cutoff:
                continue
            o = float(r[col_idx["open"]])
            h = float(r[col_idx["high"]])
            l = float(r[col_idx["low"]])
            c = float(r[col_idx["close"]])
            v = float(r[col_idx["volume"]])
            h = max(h, o, c)
            l = min(l, o, c)
            candles.append(
                Candle(open_ts=open_ts, close_ts=close_ts, open=o, high=h, low=l, close=c, volume=max(0.0, v))
            )
        candles.sort(key=lambda x: x.close_ts)
        return candles

    def fetch_orderbook_top(
        self,
        symbol: str,
        market_type: MarketType,
        *,
        fallback_ts: datetime | None = None,
    ) -> OrderBookTop:
        om_sym = OpenMarketSymbolMap.to_openmarket(symbol, market_type)
        data = self._call_tool("market_quote", {"symbol": om_sym})
        bid = float(data.get("bid") or data.get("last") or data.get("price"))
        ask = float(data.get("ask") or bid)
        if ask < bid:
            ask = bid
        bid_qty = float(data.get("bid_qty") or data.get("bidSize") or 100.0)
        ask_qty = float(data.get("ask_qty") or data.get("askSize") or 100.0)
        raw_ts = data.get("ts")
        if raw_ts:
            ts = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00")).astimezone(timezone.utc)
        else:
            ts = (fallback_ts or datetime.now(timezone.utc)).astimezone(timezone.utc)
        return OrderBookTop(ts=ts, bid=bid, ask=ask, bid_qty=bid_qty, ask_qty=ask_qty)

    @staticmethod
    def _idempotency_key(req: OrderRequest) -> str:
        raw = f"{req.symbol}:{req.market_type}:{req.side}:{req.quantity:.8f}:{req.ts.isoformat()}"
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
        return f"ase_{req.symbol}_{digest}"[:64]

    def place_order(self, req: OrderRequest) -> OrderFill:
        if req.market_type == "spot" and req.side == "sell" and not req.reduce_only:
            raise OrderExecutionError("Short spot orders are prohibited; route shorts to linear_perps")

        om_sym = OpenMarketSymbolMap.to_openmarket(req.symbol, req.market_type)
        idem_key = self._idempotency_key(req)
        args: dict[str, Any] = {
            "symbol": om_sym,
            "side": req.side,
            "type": "market",
            "quantity": req.quantity,
            "idempotency_key": idem_key,
        }
        if req.market_type == "linear_perps" and req.reduce_only:
            args["reduce_only"] = True

        data = self._call_tool("order_place", args)
        status = str(data.get("status", "error")).lower()
        reason = str(data.get("reason") or status)

        if status in ("rejected", "blocked", "no_arm", "no_tab", "expired", "relay_unavailable"):
            raise OrderExecutionError(f"OpenMarket order {status} ({reason}) for {om_sym}")

        if status in ("relay_uncertain", "error"):
            # Inspect account_orders before surfacing uncertainty
            try:
                orders_data = self._call_tool("account_orders", {"idempotency_key": idem_key})
                self._log.warning("Checked account_orders after %s: %s", status, orders_data)
            except Exception:
                pass
            raise OrderExecutionError(f"OpenMarket order uncertain ({status}: {reason}) for {om_sym}")

        fill_px = float(data.get("fill_price") or data.get("average") or data.get("price") or req.reference_price)
        filled_qty = float(data.get("filled_qty") or data.get("filled") or req.quantity)
        fee_usd = float(data.get("fee") or 0.0)
        slippage_usd = abs(fill_px - req.reference_price) * filled_qty
        return OrderFill(
            order_id=str(data.get("order_id") or data.get("id") or idem_key),
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
