"""Walk-Forward Backtest Engine with fees, slippage, funding, post-March-2026 cutoff guard, and BTC benchmark."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import sqrt
from typing import Any

import numpy as np

from ase.config import SystemConfig
from ase.nightly import compute_brier_score
from ase.reflex import DailyBudgetGuard, DeterministicRuleReflex, ReflexProvider, ReflexRunner
from ase.risk import AccountState, RiskEngine
from ase.schemas import Candle, FinalistSpec, OrderBookTop
from ase.state_engine import StateEngine

# Gemini 4 Neon / Hydrogen knowledge cutoff: March 2026 -> test strictly on >= 2026-04-01 UTC
MODEL_KNOWLEDGE_CUTOFF_UTC = datetime(2026, 4, 1, 0, 0, 0, tzinfo=timezone.utc)


class KnowledgeCutoffViolationError(ValueError):
    """Raised if a model-driven backtest touches data at or before March 2026."""


@dataclass(frozen=True)
class WalkForwardFold:
    fold_index: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int


@dataclass
class OpenSimPosition:
    symbol: str
    market_type: str
    side: int  # +1 long, -1 short
    quantity: float
    entry_price: float
    stop_price: float
    target_price: float
    opened_idx: int
    max_hold_candles: int
    entry_fee_usd: float
    entry_slippage_usd: float
    predicted_confidence: float
    funding_usd: float = 0.0


@dataclass
class FoldResult:
    fold_index: int
    test_start_ts: str
    test_end_ts: str
    chosen_slope_threshold: float
    trades: int
    net_return_pct: float
    btc_hold_return_pct: float
    max_drawdown_pct: float


@dataclass
class BacktestReport:
    start_ts: str
    end_ts: str
    initial_balance_usd: float
    final_equity_usd: float
    net_return_pct: float
    btc_hold_return_pct: float
    beats_btc_after_costs: bool
    annualized_sharpe: float
    btc_annualized_sharpe: float
    max_drawdown_pct: float
    btc_max_drawdown_pct: float
    total_trades: int
    win_rate: float
    brier_score: float | None
    total_fees_usd: float
    total_slippage_usd: float
    total_funding_usd: float
    kill_switch_triggered: bool
    folds: list[FoldResult] = field(default_factory=list)
    trade_log: list[dict[str, Any]] = field(default_factory=list)

    def format_summary(self) -> str:
        lines: list[str] = []
        if not self.beats_btc_after_costs:
            lines.append(
                f"WARNING: Strategy DID NOT beat holding BTC after costs "
                f"(strategy {self.net_return_pct:+.2%} vs BTC hold {self.btc_hold_return_pct:+.2%})."
            )
        else:
            lines.append(
                f"OUTPERFORMED BTC HOLD AFTER COSTS: strategy {self.net_return_pct:+.2%} "
                f"vs BTC hold {self.btc_hold_return_pct:+.2%}."
            )
        lines.append(
            f"OOS Window: {self.start_ts} -> {self.end_ts} | Folds: {len(self.folds)} | "
            f"Trades: {self.total_trades} | Win Rate: {self.win_rate:.1%}"
        )
        lines.append(
            f"Equity: ${self.initial_balance_usd:,.2f} -> ${self.final_equity_usd:,.2f} | "
            f"Max DD: {self.max_drawdown_pct:.2%} (BTC DD: {self.btc_max_drawdown_pct:.2%}) | "
            f"Sharpe: {self.annualized_sharpe:.2f} (BTC: {self.btc_annualized_sharpe:.2f})"
        )
        lines.append(
            f"Costs: fees=${self.total_fees_usd:.2f}, slippage=${self.total_slippage_usd:.2f}, "
            f"funding=${self.total_funding_usd:+.2f} | Brier: "
            f"{f'{self.brier_score:.4f}' if self.brier_score is not None else 'N/A'} | "
            f"KillSwitch: {self.kill_switch_triggered}"
        )
        return "\n".join(lines)


def build_walk_forward_folds(
    n_bars: int,
    *,
    train_bars: int = 180,
    test_bars: int = 90,
) -> list[WalkForwardFold]:
    """Generate strictly chronological, non-overlapping walk-forward train/test slices."""
    if n_bars < train_bars + test_bars:
        raise ValueError(f"Insufficient bars ({n_bars}) for train={train_bars} + test={test_bars}")
    folds: list[WalkForwardFold] = []
    idx = 0
    cursor = train_bars
    while cursor + test_bars <= n_bars:
        folds.append(
            WalkForwardFold(
                fold_index=idx,
                train_start=cursor - train_bars,
                train_end=cursor,
                test_start=cursor,
                test_end=cursor + test_bars,
            )
        )
        cursor += test_bars
        idx += 1
    return folds


def _annualized_sharpe_4h(equity_curve: list[float]) -> float:
    if len(equity_curve) < 4:
        return 0.0
    arr = np.array(equity_curve, dtype=float)
    rets = np.diff(arr) / np.maximum(arr[:-1], 1e-9)
    sd = float(np.std(rets, ddof=1))
    if sd <= 1e-12:
        return 0.0
    # 6 four-hour bars per day * 365 days = 2190 bars/year
    return float(np.mean(rets) / sd * sqrt(2190.0))


def _max_dd(equity_curve: list[float]) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    worst = 0.0
    for val in equity_curve:
        if val > peak:
            peak = val
        dd = max(0.0, 1.0 - val / max(peak, 1e-9))
        if dd > worst:
            worst = dd
    return worst


class WalkForwardBacktester:
    """Strictly causal walk-forward simulator with fees, slippage, funding, and BTC benchmark."""

    def __init__(
        self,
        cfg: SystemConfig,
        finalists: list[FinalistSpec],
        *,
        reflex_provider: ReflexProvider | None = None,
    ) -> None:
        self.cfg = cfg
        self.finalists = finalists
        self.custom_provider = reflex_provider
        self.state_engine = StateEngine(
            max_tokens=cfg.reflex.max_snapshot_tokens,
            stale_feed_max_age_sec=cfg.exchange.stale_feed_max_age_sec,
            clock_skew_max_ms=cfg.exchange.clock_skew_max_ms,
        )
        self.risk_engine = RiskEngine(cfg.risk)

    def _align_series(
        self, candles_by_symbol: dict[str, list[Candle]]
    ) -> tuple[list[datetime], dict[str, list[Candle]]]:
        if "BTCUSDT" not in candles_by_symbol:
            raise ValueError("BTCUSDT candles are required for regime context and BTC buy-and-hold benchmark")
        active_syms = ["BTCUSDT"] + [f.symbol for f in self.finalists if f.symbol in candles_by_symbol]
        if len(active_syms) < 2:
            raise ValueError("At least one finalist symbol must be present alongside BTCUSDT")

        ts_sets = [{c.open_ts for c in candles_by_symbol[s]} for s in active_syms]
        common_ts = sorted(set.intersection(*ts_sets))
        if len(common_ts) < 270:
            raise ValueError(f"Insufficient aligned 4h bars across universe ({len(common_ts)} < 270)")

        aligned: dict[str, list[Candle]] = {}
        for s in active_syms:
            lookup = {c.open_ts: c for c in candles_by_symbol[s]}
            aligned[s] = [lookup[t] for t in common_ts]
        return common_ts, aligned

    def _simulate_slice(
        self,
        *,
        aligned: dict[str, list[Candle]],
        funding_by_symbol: dict[str, dict[datetime, float]],
        start_idx: int,
        end_idx: int,
        account: AccountState,
        provider: ReflexProvider,
        decision_count_start: int,
        brier_samples: list[tuple[float, int]],
    ) -> tuple[list[float], list[dict[str, Any]], float, float, float, int]:
        """Simulate causal bar-by-bar trading on [start_idx, end_idx) without lookahead."""
        runner = ReflexRunner(provider, DailyBudgetGuard(self.cfg.reflex.daily_budget_usd))
        btc = aligned["BTCUSDT"]
        fin_by_sym = {f.symbol: f for f in self.finalists if f.symbol in aligned}

        cash = account.equity
        positions: dict[str, OpenSimPosition] = {}
        equity_curve: list[float] = []
        trades: list[dict[str, Any]] = []
        total_fees = 0.0
        total_slip = 0.0
        total_funding = 0.0
        decision_count = decision_count_start
        current_day: str | None = None

        for i in range(start_idx, end_idx):
            bar_ts = btc[i].open_ts
            day_str = bar_ts.strftime("%Y-%m-%d")
            if day_str != current_day:
                current_day = day_str
                # Mark equity at UTC day boundary
                mtm = cash + sum(
                    p.side * p.quantity * (aligned[sym][i].open - p.entry_price)
                    for sym, p in positions.items()
                )
                account.day_start_equity = mtm
                account.new_risk_paused = False

            # 1. Settle funding and check stops/targets/time-exits on open positions
            to_close: list[tuple[str, float, str]] = []
            for sym, pos in positions.items():
                bar = aligned[sym][i]
                if pos.market_type == "linear_perps":
                    rate = funding_by_symbol.get(sym, {}).get(bar.open_ts, 0.0)
                    if rate:
                        payment = -pos.side * pos.quantity * bar.open * rate
                        pos.funding_usd += payment
                        cash += payment
                        total_funding += payment

                # Check open gap stop, intraday adverse extreme stop, target, max hold, or fold end
                hit_stop = (pos.side == 1 and bar.low <= pos.stop_price) or (
                    pos.side == -1 and bar.high >= pos.stop_price
                )
                hit_target = (pos.side == 1 and bar.high >= pos.target_price) or (
                    pos.side == -1 and bar.low <= pos.target_price
                )
                if hit_stop:
                    exit_ref = bar.open if (
                        (pos.side == 1 and bar.open <= pos.stop_price)
                        or (pos.side == -1 and bar.open >= pos.stop_price)
                    ) else pos.stop_price
                    to_close.append((sym, exit_ref, "stop"))
                elif hit_target:
                    to_close.append((sym, pos.target_price, "target"))
                elif i - pos.opened_idx >= pos.max_hold_candles:
                    to_close.append((sym, bar.close, "time_exit"))
                elif i == end_idx - 1:
                    to_close.append((sym, bar.close, "fold_end"))

            for sym, exit_ref, reason in to_close:
                pos = positions.pop(sym)
                fee_bps, slip_bps = self.cfg.costs.side_fee_and_slippage_bps(sym, pos.market_type)  # type: ignore[arg-type]
                exit_fill = exit_ref * (1.0 - pos.side * slip_bps / 10_000.0)
                exit_fee = pos.quantity * exit_fill * (fee_bps / 10_000.0)
                exit_slip = pos.quantity * exit_ref * (slip_bps / 10_000.0)
                gross_pnl = pos.side * pos.quantity * (exit_fill - pos.entry_price)
                cash += gross_pnl - exit_fee
                net_pnl = gross_pnl + pos.funding_usd - pos.entry_fee_usd - exit_fee
                total_fees += exit_fee
                total_slip += exit_slip
                outcome = 1 if net_pnl > 0 else 0
                brier_samples.append((pos.predicted_confidence, outcome))
                trades.append(
                    {
                        "symbol": sym,
                        "market_type": pos.market_type,
                        "side": "long" if pos.side == 1 else "short",
                        "entry_ts": aligned[sym][pos.opened_idx].open_ts.isoformat(),
                        "exit_ts": aligned[sym][i].close_ts.isoformat(),
                        "entry_price": pos.entry_price,
                        "exit_price": exit_fill,
                        "net_pnl_usd": net_pnl,
                        "fees_usd": pos.entry_fee_usd + exit_fee,
                        "slippage_usd": pos.entry_slippage_usd + exit_slip,
                        "funding_usd": pos.funding_usd,
                        "exit_reason": reason,
                        "confidence": pos.predicted_confidence,
                    }
                )

            # Update account MTM equity before evaluating new entries
            gross_exp = sum(p.quantity * aligned[sym][i].open for sym, p in positions.items())
            mtm_equity = cash + sum(
                p.side * p.quantity * (aligned[sym][i].open - p.entry_price)
                for sym, p in positions.items()
            )
            account.open_positions = len(positions)
            account.gross_exposure_usd = gross_exp
            account.update_equity(mtm_equity)

            # 2. Evaluate new signals strictly using COMPLETED bars [0 .. i-1] -> fill at bar i open
            if i >= 45 and i < end_idx - 1 and not account.kill_switch_triggered:
                as_of = btc[i - 1].close_ts
                btc_hist = btc[max(0, i - 45) : i]
                rolling_brier = (
                    compute_brier_score(brier_samples[-100:]) if len(brier_samples) >= 20 else None
                )
                emp_win_rate = (
                    sum(o for _, o in brier_samples[-100:]) / len(brier_samples[-100:])
                    if len(brier_samples) >= 20
                    else None
                )
                for sym, fin in fin_by_sym.items():
                    if sym in positions:
                        continue
                    hist = aligned[sym][max(0, i - 45) : i]
                    prev_bar = hist[-1]
                    _, slip_bps = self.cfg.costs.side_fee_and_slippage_bps(sym, fin.market_type)
                    half_spd = (slip_bps * 0.3) / 10_000.0
                    # Causal orderbook proxy derived from completed bar close/body
                    body_ratio = (prev_bar.close - prev_bar.open) / max(prev_bar.high - prev_bar.low, 1e-9)
                    bid_qty = 100.0 * (1.0 + max(-0.8, min(0.8, body_ratio)))
                    ask_qty = 100.0 * (1.0 - max(-0.8, min(0.8, body_ratio)))
                    ob = OrderBookTop(
                        ts=as_of,
                        bid=prev_bar.close * (1.0 - half_spd),
                        ask=prev_bar.close * (1.0 + half_spd),
                        bid_qty=max(1.0, bid_qty),
                        ask_qty=max(1.0, ask_qty),
                    )
                    # Latest funding strictly <= as_of
                    sym_fund = funding_by_symbol.get(sym, {})
                    past_fund_ts = [t for t in sym_fund if t <= as_of]
                    fund_rate = sym_fund[max(past_fund_ts)] if past_fund_ts else 0.0
                    fund_ts = max(past_fund_ts) if past_fund_ts else as_of

                    snap = self.state_engine.build_snapshot(
                        symbol=sym,
                        as_of_ts=as_of,
                        local_now_ts=as_of,
                        exchange_server_ts=as_of,
                        candles=hist,
                        btc_candles=btc_hist,
                        orderbook=ob,
                        funding_rate=fund_rate,
                        funding_ts=fund_ts,
                        account_equity=account.equity,
                        peak_equity=account.peak_equity,
                        day_start_equity=account.day_start_equity,
                        gross_exposure_usd=account.gross_exposure_usd,
                        open_positions=account.open_positions,
                        daily_realized_pnl=account.equity - account.day_start_equity,
                    )
                    outcome = runner.evaluate(snap)
                    if outcome.skipped or outcome.decision is None:
                        continue
                    decision_count += 1
                    dec = outcome.decision

                    # Enforce finalist directional permissions & market-type short restriction
                    if dec.direction not in fin.allowed_directions:
                        continue
                    if dec.direction == "short" and fin.market_type == "spot":
                        continue

                    fee_bps, slip_bps = self.cfg.costs.side_fee_and_slippage_bps(sym, fin.market_type)
                    entry_ref = aligned[sym][i].open
                    verdict = self.risk_engine.evaluate(
                        decision=dec,
                        account=account,
                        price=entry_ref,
                        stop_loss_pct=fin.stop_loss_pct,
                        fee_bps=fee_bps,
                        slippage_bps=slip_bps,
                        decision_count=decision_count,
                        rolling_brier=rolling_brier,
                        baseline_brier=self.cfg.risk.baseline_brier_score,
                        win_rate=emp_win_rate,
                        payoff_ratio=fin.take_profit_pct / fin.stop_loss_pct,
                    )
                    if not verdict.approved:
                        continue

                    side = 1 if dec.direction == "long" else -1
                    entry_fill = entry_ref * (1.0 + side * slip_bps / 10_000.0)
                    qty = verdict.notional_usd / entry_fill
                    entry_fee = qty * entry_fill * (fee_bps / 10_000.0)
                    entry_slip = qty * entry_ref * (slip_bps / 10_000.0)
                    cash -= entry_fee
                    total_fees += entry_fee
                    total_slip += entry_slip
                    positions[sym] = OpenSimPosition(
                        symbol=sym,
                        market_type=fin.market_type,
                        side=side,
                        quantity=qty,
                        entry_price=entry_fill,
                        stop_price=entry_fill * (1.0 - side * fin.stop_loss_pct),
                        target_price=entry_fill * (1.0 + side * fin.take_profit_pct),
                        opened_idx=i,
                        max_hold_candles=fin.max_hold_candles,
                        entry_fee_usd=entry_fee,
                        entry_slippage_usd=entry_slip,
                        predicted_confidence=dec.confidence,
                    )
                    account.open_positions = len(positions)
                    account.gross_exposure_usd += qty * entry_fill

            end_mtm = cash + sum(
                p.side * p.quantity * (aligned[sym][i].close - p.entry_price)
                for sym, p in positions.items()
            )
            account.update_equity(end_mtm)
            equity_curve.append(end_mtm)

        return equity_curve, trades, total_fees, total_slip, total_funding, decision_count

    def run(
        self,
        *,
        candles_by_symbol: dict[str, list[Candle]],
        funding_by_symbol: dict[str, dict[datetime, float]],
        uses_llm_decisions: bool = True,
        train_bars: int = 180,
        test_bars: int = 90,
    ) -> BacktestReport:
        common_ts, aligned = self._align_series(candles_by_symbol)
        if uses_llm_decisions and common_ts[0] < MODEL_KNOWLEDGE_CUTOFF_UTC:
            raise KnowledgeCutoffViolationError(
                f"Model-driven backtest starts at {common_ts[0].isoformat()} which is before "
                f"the Gemini 4 knowledge cutoff ({MODEL_KNOWLEDGE_CUTOFF_UTC.isoformat()}). "
                "Test strictly on post-March-2026 data."
            )

        folds = build_walk_forward_folds(len(common_ts), train_bars=train_bars, test_bars=test_bars)
        allowed_map = {f.symbol: list(f.allowed_directions) for f in self.finalists}
        candidate_slopes = [0.0018, 0.0025, 0.0035]

        oos_account = AccountState(
            equity=self.cfg.account.initial_paper_balance_usdt,
            peak_equity=self.cfg.account.initial_paper_balance_usdt,
            day_start_equity=self.cfg.account.initial_paper_balance_usdt,
        )
        oos_equity_curve: list[float] = []
        oos_trades: list[dict[str, Any]] = []
        oos_brier_samples: list[tuple[float, int]] = []
        fold_results: list[FoldResult] = []
        total_fees = 0.0
        total_slip = 0.0
        total_funding = 0.0
        decision_count = 0

        btc = aligned["BTCUSDT"]
        btc_fee_bps, btc_slip_bps = self.cfg.costs.side_fee_and_slippage_bps("BTCUSDT", "spot")
        btc_oos_curve: list[float] = []

        for fold in folds:
            # 1. Walk-forward in-sample selection ONLY on [train_start, train_end)
            if self.custom_provider is not None:
                chosen_provider = self.custom_provider
                chosen_slope = 0.0025
            else:
                best_score = -1e9
                chosen_slope = 0.0025
                for slope in candidate_slopes:
                    trial_acct = AccountState(
                        equity=self.cfg.account.initial_paper_balance_usdt,
                        peak_equity=self.cfg.account.initial_paper_balance_usdt,
                        day_start_equity=self.cfg.account.initial_paper_balance_usdt,
                    )
                    trial_prov = DeterministicRuleReflex(
                        allowed_directions_by_symbol=allowed_map,
                        trend_slope_threshold=slope,
                    )
                    eq, _, _, _, _, _ = self._simulate_slice(
                        aligned=aligned,
                        funding_by_symbol=funding_by_symbol,
                        start_idx=fold.train_start,
                        end_idx=fold.train_end,
                        account=trial_acct,
                        provider=trial_prov,
                        decision_count_start=0,
                        brier_samples=[],
                    )
                    score = _annualized_sharpe_4h(eq) if not trial_acct.kill_switch_triggered else -100.0
                    if score > best_score:
                        best_score = score
                        chosen_slope = slope
                chosen_provider = DeterministicRuleReflex(
                    allowed_directions_by_symbol=allowed_map,
                    trend_slope_threshold=chosen_slope,
                )

            # 2. Out-of-sample evaluation on [test_start, test_end) carrying persistent equity/kill state
            fold_start_eq = oos_account.equity
            eq_slice, tr_slice, fees, slip, fund, decision_count = self._simulate_slice(
                aligned=aligned,
                funding_by_symbol=funding_by_symbol,
                start_idx=fold.test_start,
                end_idx=fold.test_end,
                account=oos_account,
                provider=chosen_provider,
                decision_count_start=decision_count,
                brier_samples=oos_brier_samples,
            )
            oos_equity_curve.extend(eq_slice)
            oos_trades.extend(tr_slice)
            total_fees += fees
            total_slip += slip
            total_funding += fund

            fold_btc_entry = btc[fold.test_start].open * (1.0 + btc_slip_bps / 10_000.0)
            fold_btc_exit = btc[fold.test_end - 1].close * (1.0 - btc_slip_bps / 10_000.0)
            fold_btc_ret = (fold_btc_exit * (1.0 - btc_fee_bps / 10_000.0)) / (
                fold_btc_entry * (1.0 + btc_fee_bps / 10_000.0)
            ) - 1.0
            fold_strat_ret = (eq_slice[-1] / max(fold_start_eq, 1e-9)) - 1.0

            fold_results.append(
                FoldResult(
                    fold_index=fold.fold_index,
                    test_start_ts=btc[fold.test_start].open_ts.isoformat(),
                    test_end_ts=btc[fold.test_end - 1].close_ts.isoformat(),
                    chosen_slope_threshold=chosen_slope,
                    trades=len(tr_slice),
                    net_return_pct=fold_strat_ret,
                    btc_hold_return_pct=fold_btc_ret,
                    max_drawdown_pct=_max_dd(eq_slice),
                )
            )

        # Full combined OOS BTC buy-and-hold comparator after entry/exit spot fee + slippage
        oos_start = folds[0].test_start
        oos_end = folds[-1].test_end
        btc_buy_px = btc[oos_start].open * (1.0 + btc_slip_bps / 10_000.0)
        btc_qty = (self.cfg.account.initial_paper_balance_usdt * (1.0 - btc_fee_bps / 10_000.0)) / btc_buy_px
        for idx in range(oos_start, oos_end):
            mark = btc[idx].close
            if idx == oos_end - 1:
                mark = mark * (1.0 - btc_slip_bps / 10_000.0) * (1.0 - btc_fee_bps / 10_000.0)
            btc_oos_curve.append(btc_qty * mark)

        init_bal = self.cfg.account.initial_paper_balance_usdt
        final_eq = oos_equity_curve[-1] if oos_equity_curve else init_bal
        net_ret = final_eq / init_bal - 1.0
        btc_ret = (btc_oos_curve[-1] / init_bal - 1.0) if btc_oos_curve else 0.0
        wins = sum(1 for t in oos_trades if t["net_pnl_usd"] > 0)
        win_rate = wins / len(oos_trades) if oos_trades else 0.0
        brier = compute_brier_score(oos_brier_samples) if oos_brier_samples else None

        return BacktestReport(
            start_ts=btc[oos_start].open_ts.isoformat(),
            end_ts=btc[oos_end - 1].close_ts.isoformat(),
            initial_balance_usd=init_bal,
            final_equity_usd=final_eq,
            net_return_pct=net_ret,
            btc_hold_return_pct=btc_ret,
            beats_btc_after_costs=net_ret > btc_ret,
            annualized_sharpe=_annualized_sharpe_4h(oos_equity_curve),
            btc_annualized_sharpe=_annualized_sharpe_4h(btc_oos_curve),
            max_drawdown_pct=_max_dd(oos_equity_curve),
            btc_max_drawdown_pct=_max_dd(btc_oos_curve),
            total_trades=len(oos_trades),
            win_rate=win_rate,
            brier_score=brier,
            total_fees_usd=total_fees,
            total_slippage_usd=total_slip,
            total_funding_usd=total_funding,
            kill_switch_triggered=oos_account.kill_switch_triggered,
            folds=fold_results,
            trade_log=oos_trades,
        )
