"""Hard Code-Owned Risk Layer — NO model or config file can override these limits."""

from __future__ import annotations

from dataclasses import dataclass

from ase.config import RiskSection
from ase.schemas import ReflexDecision

# IMMUTABLE HARD LIMITS — checked in Python code before EVERY order
HARD_MAX_RISK_PER_TRADE_PCT: float = 0.01
HARD_MAX_DAILY_LOSS_PCT: float = 0.05
HARD_MAX_OPEN_POSITIONS: int = 3
HARD_MAX_LEVERAGE: float = 2.0
HARD_KILL_SWITCH_DRAWDOWN_PCT: float = 0.15
HARD_MIN_SETUP_QUALITY: int = 2
HARD_MIN_CONFIDENCE_TO_TRADE: float = 0.80
HARD_PAUSE_CONFIDENCE_THRESHOLD: float = 0.60
HARD_FIXED_MIN_DECISIONS: int = 200
HARD_MAX_KELLY_FRACTION: float = 0.25


@dataclass
class AccountState:
    """Mutable account risk state persisted across candles."""

    equity: float
    peak_equity: float
    day_start_equity: float
    open_positions: int = 0
    gross_exposure_usd: float = 0.0
    kill_switch_triggered: bool = False
    new_risk_paused: bool = False

    def update_equity(self, new_equity: float) -> float:
        self.equity = float(new_equity)
        if self.equity > self.peak_equity:
            self.peak_equity = self.equity
        drawdown = self.drawdown_pct
        if drawdown >= HARD_KILL_SWITCH_DRAWDOWN_PCT:
            # Permanent kill switch: NEVER auto-resets
            self.kill_switch_triggered = True
            self.new_risk_paused = True
        return drawdown

    @property
    def drawdown_pct(self) -> float:
        if self.peak_equity <= 0:
            return 1.0
        return max(0.0, 1.0 - self.equity / self.peak_equity)

    @property
    def daily_loss_pct(self) -> float:
        if self.day_start_equity <= 0:
            return 1.0
        return max(0.0, 1.0 - self.equity / self.day_start_equity)


@dataclass(frozen=True)
class RiskVerdict:
    approved: bool
    reason: str
    notional_usd: float = 0.0
    quantity: float = 0.0
    sizing_mode: str = "none"
    pause_new_risk: bool = False
    alert_telegram: bool = False


class RiskEngine:
    """Deterministic risk gate & position sizer. Config may tighten limits, never relax them."""

    def __init__(self, cfg: RiskSection) -> None:
        self.max_risk_per_trade_pct = min(cfg.max_risk_per_trade_pct, HARD_MAX_RISK_PER_TRADE_PCT)
        self.max_daily_loss_pct = min(cfg.max_daily_loss_pct, HARD_MAX_DAILY_LOSS_PCT)
        self.max_open_positions = min(cfg.max_open_positions, HARD_MAX_OPEN_POSITIONS)
        self.max_leverage = min(cfg.max_leverage, HARD_MAX_LEVERAGE)
        self.kill_switch_drawdown_pct = min(cfg.kill_switch_drawdown_pct, HARD_KILL_SWITCH_DRAWDOWN_PCT)
        self.min_setup_quality = max(cfg.min_setup_quality, HARD_MIN_SETUP_QUALITY)
        self.min_confidence_to_trade = max(cfg.min_confidence_to_trade, HARD_MIN_CONFIDENCE_TO_TRADE)
        self.pause_confidence_threshold = max(cfg.pause_confidence_threshold, HARD_PAUSE_CONFIDENCE_THRESHOLD)
        self.fixed_min_decision_count = max(cfg.fixed_min_decision_count, HARD_FIXED_MIN_DECISIONS)
        self.fixed_min_notional_pct = min(max(cfg.fixed_min_notional_pct, 0.01), 0.10)
        self.max_kelly_fraction = min(cfg.max_kelly_fraction, HARD_MAX_KELLY_FRACTION)
        self.baseline_brier_score = cfg.baseline_brier_score

    def evaluate(
        self,
        decision: ReflexDecision,
        account: AccountState,
        price: float,
        stop_loss_pct: float,
        fee_bps: float,
        slippage_bps: float,
        *,
        decision_count: int,
        rolling_brier: float | None = None,
        baseline_brier: float | None = None,
        win_rate: float | None = None,
        payoff_ratio: float = 1.5,
    ) -> RiskVerdict:
        # 1. Permanent drawdown kill switch check (15% hard cap)
        if account.drawdown_pct >= self.kill_switch_drawdown_pct:
            account.kill_switch_triggered = True
            account.new_risk_paused = True
        if account.kill_switch_triggered:
            return RiskVerdict(
                approved=False,
                reason=f"kill_switch_engaged: drawdown={account.drawdown_pct:.2%}",
                pause_new_risk=True,
                alert_telegram=True,
            )

        # 2. Confidence < 0.60 or regime == 'crisis' -> pause new risk & alert Telegram
        if decision.regime == "crisis" or decision.confidence < self.pause_confidence_threshold:
            account.new_risk_paused = True
            return RiskVerdict(
                approved=False,
                reason=f"risk_paused: regime={decision.regime}, confidence={decision.confidence:.3f}",
                pause_new_risk=True,
                alert_telegram=True,
            )

        # 3. Non-actionable REFLEX hold
        if decision.action == "hold" or decision.direction == "neutral":
            return RiskVerdict(approved=False, reason="reflex_hold")

        # 4. Daily loss hard cap (5% UTC day max)
        if account.daily_loss_pct >= self.max_daily_loss_pct:
            return RiskVerdict(
                approved=False,
                reason=f"daily_loss_limit: {account.daily_loss_pct:.2%} >= {self.max_daily_loss_pct:.2%}",
            )

        # 5. Concurrent open positions cap (3 max)
        if account.open_positions >= self.max_open_positions:
            return RiskVerdict(
                approved=False,
                reason=f"max_open_positions: {account.open_positions} >= {self.max_open_positions}",
            )

        # 6. REFLEX entry quality, confidence, and risk_state gates
        if decision.setup_quality < self.min_setup_quality:
            return RiskVerdict(
                approved=False,
                reason=f"setup_quality_below_min: {decision.setup_quality} < {self.min_setup_quality}",
            )
        if decision.confidence <= self.min_confidence_to_trade:
            return RiskVerdict(
                approved=False,
                reason=f"confidence_below_gate: {decision.confidence:.3f} <= {self.min_confidence_to_trade:.2f}",
            )
        if decision.risk_state != "safe":
            return RiskVerdict(
                approved=False,
                reason=f"risk_state_not_safe: {decision.risk_state}",
            )

        if price <= 0 or stop_loss_pct <= 0 or account.equity <= 0:
            return RiskVerdict(approved=False, reason="invalid_price_stop_or_equity")

        # 7. Code-owned position sizing:
        #    - First 200 decisions -> fixed minimum size
        #    - After 200 decisions -> up to quarter Kelly ONLY while Brier score beats baseline
        #    - ALWAYS bounded by 1% risk per trade (incl. round-trip fees + slippage) and 2x gross leverage
        round_trip_cost_pct = 2.0 * (fee_bps + slippage_bps) / 10_000.0
        effective_stop_pct = stop_loss_pct + round_trip_cost_pct
        max_trade_risk_usd = account.equity * self.max_risk_per_trade_pct
        # Also ensure a single stop cannot breach the remaining daily loss budget
        remaining_daily_usd = max(
            0.0, account.equity - account.day_start_equity * (1.0 - self.max_daily_loss_pct)
        )
        allowed_risk_usd = min(max_trade_risk_usd, remaining_daily_usd)
        if allowed_risk_usd <= 0:
            return RiskVerdict(approved=False, reason="daily_loss_budget_exhausted")

        risk_capped_notional = allowed_risk_usd / effective_stop_pct
        max_total_exposure = account.equity * self.max_leverage
        leverage_headroom = max(0.0, max_total_exposure - account.gross_exposure_usd)
        if leverage_headroom <= 0:
            return RiskVerdict(approved=False, reason="max_leverage_reached")

        fixed_min_notional = account.equity * self.fixed_min_notional_pct
        brier_base = baseline_brier if baseline_brier is not None else self.baseline_brier_score

        if (
            decision_count >= self.fixed_min_decision_count
            and rolling_brier is not None
            and rolling_brier < brier_base
            and win_rate is not None
            and payoff_ratio > 0
        ):
            full_kelly = max(0.0, win_rate - (1.0 - win_rate) / payoff_ratio)
            kelly_alloc = min(self.max_kelly_fraction * full_kelly, self.max_kelly_fraction)
            target_notional = max(fixed_min_notional, account.equity * kelly_alloc * 10.0)
            sizing_mode = "quarter_kelly"
        else:
            target_notional = fixed_min_notional
            sizing_mode = "fixed_min"

        final_notional = min(target_notional, risk_capped_notional, leverage_headroom)
        if final_notional <= 1.0:
            return RiskVerdict(approved=False, reason="sized_notional_too_small")

        return RiskVerdict(
            approved=True,
            reason="approved",
            notional_usd=float(final_notional),
            quantity=float(final_notional / price),
            sizing_mode=sizing_mode,
        )
