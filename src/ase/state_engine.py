"""Causal State Engine: converts order book, candles, funding, and account state into a <400-token numeric snapshot."""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone

import numpy as np

from ase.schemas import Candle, MarketSnapshot, OrderBookTop


class CausalLeakageError(ValueError):
    """Raised when any input timestamp is strictly in the future relative to as_of_ts."""


class StaleFeedError(RuntimeError):
    """Raised when market data exceeds the configured freshness threshold."""


class ClockSkewError(RuntimeError):
    """Raised when exchange server clock and local UTC clock diverge beyond tolerance."""


class SnapshotTokenLimitError(ValueError):
    """Raised if the serialized REFLEX payload equals or exceeds the token budget."""


_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[^\sA-Za-z0-9_]")


def estimate_tokens(text: str) -> int:
    """Conservative upper-bound token estimate combining char-ratio and punctuation splits."""
    char_bound = math.ceil(len(text) / 3.2)
    regex_bound = len(_TOKEN_RE.findall(text))
    return max(char_bound, regex_bound)


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _ema(values: np.ndarray, span: int) -> np.ndarray:
    alpha = 2.0 / (span + 1.0)
    out = np.empty_like(values, dtype=float)
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = alpha * values[i] + (1.0 - alpha) * out[i - 1]
    return out


class StateEngine:
    """Computes all microstructure and portfolio numbers in Python code — never in the LLM."""

    def __init__(
        self,
        *,
        max_tokens: int = 400,
        stale_feed_max_age_sec: int = 900,
        clock_skew_max_ms: int = 5000,
    ) -> None:
        self.max_tokens = max_tokens
        self.stale_feed_max_age_sec = stale_feed_max_age_sec
        self.clock_skew_max_ms = clock_skew_max_ms

    def build_snapshot(
        self,
        *,
        symbol: str,
        as_of_ts: datetime,
        local_now_ts: datetime,
        exchange_server_ts: datetime,
        candles: list[Candle],
        btc_candles: list[Candle],
        orderbook: OrderBookTop,
        funding_rate: float,
        funding_ts: datetime,
        account_equity: float,
        peak_equity: float,
        day_start_equity: float,
        gross_exposure_usd: float,
        open_positions: int,
        daily_realized_pnl: float,
    ) -> MarketSnapshot:
        as_of = _utc(as_of_ts)
        local_now = _utc(local_now_ts)
        exch_ts = _utc(exchange_server_ts)
        ob_ts = _utc(orderbook.ts)
        fund_ts = _utc(funding_ts)

        # 1. Clock skew check before any computation or order
        skew_ms = abs((exch_ts - local_now).total_seconds() * 1000.0)
        if skew_ms > self.clock_skew_max_ms:
            raise ClockSkewError(
                f"Exchange/local clock skew {skew_ms:.0f}ms exceeds limit {self.clock_skew_max_ms}ms"
            )

        # 2. Strict causal timestamp checks — ZERO future leakage
        if len(candles) < 21 or len(btc_candles) < 21:
            raise ValueError("Need at least 21 completed candles for asset and BTC benchmark")
        for idx, c in enumerate(candles):
            if _utc(c.close_ts) > as_of:
                raise CausalLeakageError(
                    f"Candle {symbol}[{idx}] close_ts={c.close_ts.isoformat()} > as_of={as_of.isoformat()}"
                )
            if idx > 0 and _utc(c.close_ts) <= _utc(candles[idx - 1].close_ts):
                raise CausalLeakageError(f"Non-monotonic candle timestamps for {symbol}")
        for idx, c in enumerate(btc_candles):
            if _utc(c.close_ts) > as_of:
                raise CausalLeakageError(
                    f"BTC candle[{idx}] close_ts={c.close_ts.isoformat()} > as_of={as_of.isoformat()}"
                )
        if ob_ts > as_of:
            raise CausalLeakageError(f"Orderbook ts={ob_ts.isoformat()} > as_of={as_of.isoformat()}")
        if fund_ts > as_of:
            raise CausalLeakageError(f"Funding ts={fund_ts.isoformat()} > as_of={as_of.isoformat()}")

        # 3. Feed staleness check
        ob_age = (local_now - ob_ts).total_seconds()
        if ob_age > self.stale_feed_max_age_sec:
            raise StaleFeedError(
                f"Orderbook feed for {symbol} is stale ({ob_age:.1f}s > {self.stale_feed_max_age_sec}s)"
            )

        # 4. Code-computed numeric features (spread, imbalance, volatility, exposure, drawdown)
        closes = np.array([c.close for c in candles], dtype=float)
        btc_closes = np.array([c.close for c in btc_candles], dtype=float)
        last_close = float(closes[-1])

        ret_4h = float(closes[-1] / closes[-2] - 1.0)
        ret_24h = float(closes[-1] / closes[-7] - 1.0) if len(closes) >= 7 else ret_4h
        idx_7d = max(0, len(closes) - 43)
        ret_7d = float(closes[-1] / closes[idx_7d] - 1.0)
        btc_idx_7d = max(0, len(btc_closes) - 43)
        btc_ret_7d = float(btc_closes[-1] / btc_closes[btc_idx_7d] - 1.0)

        ema20 = _ema(closes[-40:], span=20)
        ema_slope_pct = float((ema20[-1] - ema20[-2]) / max(ema20[-2], 1e-12))

        log_rets = np.diff(np.log(closes[-21:]))
        realized_vol_20 = float(np.std(log_rets, ddof=1)) if len(log_rets) > 1 else 0.0

        mid = 0.5 * (orderbook.bid + orderbook.ask)
        spread_bps = float((orderbook.ask - orderbook.bid) / max(mid, 1e-12) * 10_000.0)
        total_qty = orderbook.bid_qty + orderbook.ask_qty
        imbalance = float((orderbook.bid_qty - orderbook.ask_qty) / total_qty) if total_qty > 0 else 0.0

        safe_equity = max(account_equity, 1e-9)
        safe_peak = max(peak_equity, safe_equity)
        safe_day_start = max(day_start_equity, 1e-9)
        gross_exposure_x = float(gross_exposure_usd / safe_equity)
        drawdown_pct = float(max(0.0, 1.0 - safe_equity / safe_peak))
        daily_pnl_pct = float((safe_equity - safe_day_start) / safe_day_start)
        _ = daily_realized_pnl  # tracked in equity delta

        # Preliminary snapshot to verify strict <400 token budget
        prelim = MarketSnapshot(
            symbol=symbol,
            as_of_ts=as_of,
            last_close=last_close,
            ret_4h=ret_4h,
            ret_24h=ret_24h,
            ret_7d=ret_7d,
            ema_slope_pct=ema_slope_pct,
            btc_ret_7d=btc_ret_7d,
            spread_bps=spread_bps,
            imbalance=imbalance,
            realized_vol_20=realized_vol_20,
            funding_bps=float(funding_rate * 10_000.0),
            gross_exposure_x=gross_exposure_x,
            drawdown_pct=drawdown_pct,
            daily_pnl_pct=daily_pnl_pct,
            open_positions=open_positions,
            token_count=1,
        )
        tok = estimate_tokens(prelim.to_reflex_payload())
        if tok >= self.max_tokens:
            raise SnapshotTokenLimitError(
                f"Snapshot token count {tok} >= max limit {self.max_tokens}"
            )
        return prelim.model_copy(update={"token_count": tok})
