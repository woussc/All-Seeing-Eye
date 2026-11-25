"""Typed domain schemas for All-Seeing-Eye (REFLEX decisions, causal snapshots, finalists)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

RegimeType = Literal["trending", "mean_reverting", "high_vol", "crisis"]
DirectionType = Literal["long", "short", "neutral"]
RiskStateType = Literal["safe", "near_limit", "reduce"]
ActionType = Literal["buy", "sell", "hold"]
MarketType = Literal["spot", "linear_perps"]


class Candle(BaseModel):
    """Completed OHLCV bar with strict UTC open and close timestamps."""

    open_ts: datetime
    close_ts: datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)

    @model_validator(mode="after")
    def _validate_bounds(self) -> "Candle":
        if self.close_ts <= self.open_ts:
            raise ValueError("close_ts must be strictly after open_ts")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("OHLC high/low bounds violated")
        return self


class OrderBookTop(BaseModel):
    """Top-of-book quote with observation timestamp."""

    ts: datetime
    bid: float = Field(gt=0)
    ask: float = Field(gt=0)
    bid_qty: float = Field(ge=0)
    ask_qty: float = Field(ge=0)

    @model_validator(mode="after")
    def _validate_spread(self) -> "OrderBookTop":
        if self.ask < self.bid:
            raise ValueError("ask must be >= bid")
        return self


class MarketSnapshot(BaseModel):
    """Compact causal numeric snapshot — the ONLY input the REFLEX model ever sees."""

    symbol: str
    as_of_ts: datetime
    last_close: float
    ret_4h: float
    ret_24h: float
    ret_7d: float
    ema_slope_pct: float
    btc_ret_7d: float
    spread_bps: float
    imbalance: float
    realized_vol_20: float
    funding_bps: float
    gross_exposure_x: float
    drawdown_pct: float
    daily_pnl_pct: float
    open_positions: int
    token_count: int = Field(lt=400)

    def to_reflex_payload(self) -> str:
        """Render deterministic, compact numeric JSON (<400 tokens) for REFLEX."""
        return (
            '{"sym":"' + self.symbol + '",'
            f'"ts":"{self.as_of_ts.isoformat()}",'
            f'"px":{self.last_close:.6g},'
            f'"r4h":{self.ret_4h:.5f},'
            f'"r24h":{self.ret_24h:.5f},'
            f'"r7d":{self.ret_7d:.5f},'
            f'"ema_s":{self.ema_slope_pct:.5f},'
            f'"btc7d":{self.btc_ret_7d:.5f},'
            f'"spd_bp":{self.spread_bps:.2f},'
            f'"imb":{self.imbalance:.4f},'
            f'"vol20":{self.realized_vol_20:.5f},'
            f'"fund_bp":{self.funding_bps:.3f},'
            f'"exp_x":{self.gross_exposure_x:.3f},'
            f'"dd":{self.drawdown_pct:.4f},'
            f'"dpnl":{self.daily_pnl_pct:.4f},'
            f'"pos":{self.open_positions}}}'
        )


class ReflexDecision(BaseModel):
    """Strict typed JSON schema returned by the REFLEX layer on every candle."""

    regime: RegimeType
    direction: DirectionType
    setup_quality: int = Field(ge=0, le=3)
    risk_state: RiskStateType
    confidence: float = Field(ge=0.0, le=1.0)
    action: ActionType

    @model_validator(mode="after")
    def _validate_consistency(self) -> "ReflexDecision":
        if self.action == "buy" and self.direction == "short":
            raise ValueError("buy action conflicts with short direction")
        if self.action == "sell" and self.direction == "long":
            raise ValueError("sell action conflicts with long direction")
        return self


class FinalistSpec(BaseModel):
    """Compiled finalist trade setup produced by the BRAIN layer."""

    id: str
    symbol: str
    asset: str
    market_type: MarketType
    allowed_directions: list[DirectionType]
    preferred_direction: DirectionType
    stop_loss_pct: float = Field(gt=0, lt=0.5)
    take_profit_pct: float = Field(gt=0, lt=2.0)
    max_hold_candles: int = Field(ge=1)
    market_cap_usd: float = Field(gt=0)
    volume_24h_usd: float = Field(gt=0)
    circulating_float_pct: float = Field(ge=0, le=100)
    thesis: str
    bear_case: str
    invalidation: str
