"""REFLEX Layer: single interface for candle-level judgment (Gemini 4 Hydrogen low thinking + budget guard)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from ase.schemas import MarketSnapshot, ReflexDecision


class BudgetExceededError(RuntimeError):
    """Raised when a REFLEX model call would exceed the hard daily USD budget."""


class ReflexProvider(Protocol):
    """Single clean interface behind which all REFLEX decision providers sit."""

    model_name: str
    est_call_cost_usd: float

    def decide(self, snapshot: MarketSnapshot) -> ReflexDecision: ...


class DailyBudgetGuard:
    """Tracks UTC-daily REFLEX model spend and hard-blocks calls over limit."""

    def __init__(self, daily_limit_usd: float = 5.0) -> None:
        if daily_limit_usd <= 0:
            raise ValueError("daily_limit_usd must be positive")
        self.daily_limit_usd = float(daily_limit_usd)
        self._spend_by_day: dict[str, float] = {}

    @staticmethod
    def _day_key(ts: datetime) -> str:
        return ts.astimezone(timezone.utc).strftime("%Y-%m-%d")

    def spent_usd(self, ts: datetime) -> float:
        return self._spend_by_day.get(self._day_key(ts), 0.0)

    def remaining_usd(self, ts: datetime) -> float:
        return max(0.0, self.daily_limit_usd - self.spent_usd(ts))

    def check_can_spend(self, amount_usd: float, ts: datetime) -> None:
        projected = self.spent_usd(ts) + max(0.0, amount_usd)
        if projected > self.daily_limit_usd + 1e-12:
            raise BudgetExceededError(
                f"Daily model budget exceeded: projected ${projected:.4f} > limit ${self.daily_limit_usd:.2f}"
            )

    def record_spend(self, amount_usd: float, ts: datetime) -> float:
        self.check_can_spend(amount_usd, ts)
        key = self._day_key(ts)
        self._spend_by_day[key] = self._spend_by_day.get(key, 0.0) + max(0.0, amount_usd)
        return self._spend_by_day[key]


@dataclass(frozen=True)
class ReflexOutcome:
    decision: ReflexDecision | None
    skipped: bool
    skip_reason: str | None = None
    cost_usd: float = 0.0


REFLEX_SYSTEM_PROMPT = (
    "You are the REFLEX candle-judgment layer of a crypto trading system. "
    "You receive ONLY one compact numeric causal snapshot under 400 tokens. "
    "Output strict JSON matching the ReflexDecision schema: regime, direction, "
    "setup_quality (0-3), risk_state, confidence (0.0-1.0), and action (buy/sell/hold). "
    "Be calibrated: never emit high confidence in high_vol or crisis regimes unless "
    "trend alignment, liquidity spread, and orderbook imbalance strongly agree. "
    "You never size positions or place orders; code owns all thresholds and risk."
)


class GeminiHydrogenReflexProvider:
    """Production REFLEX provider targeting Gemini 4 Hydrogen at low thinking with JSON schema output."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model_name: str = "gemini-4-hydrogen",
        thinking_level: str = "low",
        est_input_cost_per_1m_usd: float = 0.30,
        est_output_cost_per_1m_usd: float = 1.20,
        client: Any | None = None,
    ) -> None:
        self.model_name = model_name
        self.thinking_level = thinking_level
        # Conservative per-call estimate: ~500 input tokens (system + <400 snapshot) + ~150 output tokens
        self.est_call_cost_usd = (
            500.0 / 1_000_000.0 * est_input_cost_per_1m_usd
            + 150.0 / 1_000_000.0 * est_output_cost_per_1m_usd
        )
        if client is not None:
            self._client = client
        else:
            if not api_key:
                raise RuntimeError(
                    "GEMINI_API_KEY missing: store it in external secrets dir outside the repo "
                    "to use GeminiHydrogenReflexProvider."
                )
            from google import genai

            self._client = genai.Client(api_key=api_key)

    def decide(self, snapshot: MarketSnapshot) -> ReflexDecision:
        payload = snapshot.to_reflex_payload()
        response = self._client.models.generate_content(
            model=self.model_name,
            contents=payload,
            config={
                "system_instruction": REFLEX_SYSTEM_PROMPT,
                "response_mime_type": "application/json",
                "response_schema": ReflexDecision,
                "thinking_config": {"thinking_level": self.thinking_level},
                "temperature": 0.1,
            },
        )
        raw_text = getattr(response, "text", None)
        if not raw_text:
            raise ValueError("Empty response from Gemini REFLEX model")
        return ReflexDecision.model_validate_json(raw_text)


class DeterministicRuleReflex:
    """Zero-cost causal REFLEX baseline for offline tests, paper fallback, and walk-forward audits.

    Uses strictly the numeric fields inside MarketSnapshot (zero extra lookahead inputs).
    """

    model_name = "deterministic-causal-baseline"
    est_call_cost_usd = 0.0

    def __init__(
        self,
        *,
        allowed_directions_by_symbol: dict[str, list[str]] | None = None,
        trend_slope_threshold: float = 0.0025,
        crisis_vol_threshold: float = 0.065,
        high_vol_threshold: float = 0.032,
    ) -> None:
        self._allowed = allowed_directions_by_symbol or {}
        self.trend_slope_threshold = trend_slope_threshold
        self.crisis_vol_threshold = crisis_vol_threshold
        self.high_vol_threshold = high_vol_threshold

    def decide(self, snapshot: MarketSnapshot) -> ReflexDecision:
        # 1. Classify regime strictly from snapshot volatility & BTC trend
        if snapshot.realized_vol_20 >= self.crisis_vol_threshold or snapshot.btc_ret_7d <= -0.22:
            regime = "crisis"
        elif snapshot.realized_vol_20 >= self.high_vol_threshold or abs(snapshot.ret_24h) >= 0.12:
            regime = "high_vol"
        elif abs(snapshot.ema_slope_pct) >= self.trend_slope_threshold:
            regime = "trending"
        else:
            regime = "mean_reverting"

        # 2. Classify portfolio risk_state from code-computed drawdown & exposure
        if snapshot.drawdown_pct >= 0.10 or snapshot.daily_pnl_pct <= -0.035:
            risk_state = "reduce"
        elif snapshot.drawdown_pct >= 0.06 or snapshot.gross_exposure_x >= 1.5:
            risk_state = "near_limit"
        else:
            risk_state = "safe"

        if regime == "crisis":
            return ReflexDecision(
                regime="crisis",
                direction="neutral",
                setup_quality=0,
                risk_state=risk_state,
                confidence=0.45,
                action="hold",
            )

        # 3. Directional judgment combining EMA slope, 24h momentum, relative strength vs BTC, and funding/imbalance
        allowed = self._allowed.get(snapshot.symbol, ["long", "short", "neutral"])
        rel_strength_7d = snapshot.ret_7d - snapshot.btc_ret_7d

        long_score = 0
        if snapshot.ema_slope_pct > self.trend_slope_threshold:
            long_score += 1
        if snapshot.ret_24h > 0.015 and snapshot.ret_4h > 0.0:
            long_score += 1
        if rel_strength_7d > 0.02 and snapshot.btc_ret_7d > -0.10:
            long_score += 1
        if snapshot.imbalance >= 0.05 and snapshot.funding_bps < 3.5:
            long_score += 1

        short_score = 0
        if snapshot.ema_slope_pct < -self.trend_slope_threshold:
            short_score += 1
        if snapshot.ret_24h < -0.015 and snapshot.ret_4h < 0.0:
            short_score += 1
        if rel_strength_7d < -0.02 and snapshot.btc_ret_7d < 0.08:
            short_score += 1
        if snapshot.imbalance <= -0.05 or snapshot.funding_bps <= 0.0:
            short_score += 1

        # Spread friction penalty
        wide_spread = snapshot.spread_bps > 15.0

        if long_score >= 3 and "long" in allowed and not wide_spread:
            quality = 3 if long_score == 4 else 2
            conf = 0.85 if quality == 3 else 0.82
            if regime == "high_vol":
                conf -= 0.03
            return ReflexDecision(
                regime=regime,
                direction="long",
                setup_quality=quality,
                risk_state=risk_state,
                confidence=round(min(max(conf, 0.0), 0.95), 4),
                action="buy" if conf > 0.80 else "hold",
            )

        if short_score >= 3 and "short" in allowed and not wide_spread:
            quality = 3 if short_score == 4 else 2
            conf = 0.85 if quality == 3 else 0.82
            if regime == "high_vol":
                conf -= 0.03
            return ReflexDecision(
                regime=regime,
                direction="short",
                setup_quality=quality,
                risk_state=risk_state,
                confidence=round(min(max(conf, 0.0), 0.95), 4),
                action="sell" if conf > 0.80 else "hold",
            )

        # Low-conviction neutral hold
        base_conf = 0.65 if not wide_spread else 0.55
        return ReflexDecision(
            regime=regime,
            direction="neutral",
            setup_quality=1 if max(long_score, short_score) >= 2 else 0,
            risk_state=risk_state,
            confidence=base_conf,
            action="hold",
        )


class ReflexRunner:
    """Executes REFLEX behind budget guard and skips any missed/errored candle."""

    def __init__(self, provider: ReflexProvider, budget: DailyBudgetGuard) -> None:
        self.provider = provider
        self.budget = budget
        self._log = logging.getLogger("ase.reflex")

    def evaluate(self, snapshot: MarketSnapshot) -> ReflexOutcome:
        est_cost = max(0.0, float(self.provider.est_call_cost_usd))
        # Always enforce budget check even when provider cost is 0 (if already exhausted)
        if self.budget.remaining_usd(snapshot.as_of_ts) <= 0.0:
            self._log.warning("Skipping %s candle: daily model budget exhausted", snapshot.symbol)
            return ReflexOutcome(decision=None, skipped=True, skip_reason="budget_exceeded")
        try:
            self.budget.check_can_spend(est_cost, snapshot.as_of_ts)
        except BudgetExceededError:
            self._log.warning("Skipping %s candle: daily model budget exceeded", snapshot.symbol)
            return ReflexOutcome(decision=None, skipped=True, skip_reason="budget_exceeded")

        try:
            decision = self.provider.decide(snapshot)
            self.budget.record_spend(est_cost, snapshot.as_of_ts)
            return ReflexOutcome(decision=decision, skipped=False, cost_usd=est_cost)
        except Exception as exc:
            self._log.warning("Skipping %s candle due to REFLEX miss: %s", snapshot.symbol, exc)
            return ReflexOutcome(decision=None, skipped=True, skip_reason=f"reflex_miss:{type(exc).__name__}:{exc}")
