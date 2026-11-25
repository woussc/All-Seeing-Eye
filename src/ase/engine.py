"""24/7 Trading Engine runtime, /healthz HTTP health server, and paper/testnet execution cycle."""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from ase.alerts import TelegramAlerter, configure_logging
from ase.config import (
    SystemConfig,
    assert_live_approval_gate,
    is_symbol_allowed,
    read_external_secret,
)
from ase.exchange import (
    ExchangeAdapter,
    OrderRequest,
    create_exchange_adapter,
    reconcile_positions,
)
from ase.reflex import (
    DailyBudgetGuard,
    DeterministicRuleReflex,
    GeminiHydrogenReflexProvider,
    ReflexProvider,
    ReflexRunner,
)
from ase.risk import AccountState, RiskEngine
from ase.schemas import Candle, FinalistSpec, OrderBookTop
from ase.state_engine import StateEngine
from ase.storage import StateStore


class HealthServer:
    """Lightweight /healthz HTTP endpoint for Docker Compose health checks."""

    def __init__(self, *, host: str, port: int, status_fn: Callable[[], dict[str, Any]]) -> None:
        self._host = host
        self._port = port
        self._status_fn = status_fn
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        if self._server is None:
            return self._port
        return int(self._server.server_address[1])

    def start(self) -> None:
        status_fn = self._status_fn

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.path not in ("/healthz", "/health"):
                    self.send_response(404)
                    self.end_headers()
                    return
                body = json.dumps(status_fn()).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return  # suppress noisy access logs

        self._server = ThreadingHTTPServer((self._host, self._port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


class TradingEngine:
    """Coordinates state snapshots, REFLEX decisions, hard risk checks, orders, reconciliation, and persistence."""

    def __init__(
        self,
        cfg: SystemConfig,
        finalists: list[FinalistSpec],
        *,
        store: StateStore | None = None,
        exchange: ExchangeAdapter | None = None,
        reflex_provider: ReflexProvider | None = None,
        alerter: TelegramAlerter | None = None,
    ) -> None:
        assert_live_approval_gate(cfg)
        self.cfg = cfg
        self.finalists = [
            f
            for f in finalists
            if is_symbol_allowed(
                f.symbol,
                cfg.universe.off_limits,
                market_cap_usd=f.market_cap_usd,
                volume_24h_usd=f.volume_24h_usd,
            )[0]
        ]

        # Read optional external secrets safely outside the repository
        secrets_loaded: list[str] = []
        tg_token = None
        tg_chat = None
        gemini_key = None
        binance_key = None
        binance_secret = None
        om_token = None
        try:
            tg_token = read_external_secret("telegram_bot_token", cfg.system.secrets_dir, env_fallback="TELEGRAM_BOT_TOKEN")
            tg_chat = read_external_secret("telegram_chat_id", cfg.system.secrets_dir, env_fallback="TELEGRAM_CHAT_ID")
            gemini_key = read_external_secret("gemini_api_key", cfg.system.secrets_dir, env_fallback="GEMINI_API_KEY")
            binance_key = read_external_secret("binance_api_key", cfg.system.secrets_dir, env_fallback="BINANCE_API_KEY")
            binance_secret = read_external_secret("binance_api_secret", cfg.system.secrets_dir, env_fallback="BINANCE_API_SECRET")
            om_token = read_external_secret(
                cfg.openmarket.secret_name,
                cfg.system.secrets_dir,
                env_fallback="OPENMARKET_MCP_TOKEN",
            )
            for s in (tg_token, gemini_key, binance_key, binance_secret, om_token):
                if s:
                    secrets_loaded.append(s)
        except Exception:
            pass

        redactor = configure_logging(secrets_loaded)
        self._log = logging.getLogger("ase.engine")
        self.alerter = alerter or TelegramAlerter(tg_token, tg_chat, redactor=redactor)

        self.store = store or StateStore(cfg.system.db_path)
        self.store.initialize()
        if not self.store.list_policy_versions():
            self.store.save_policy_version(
                version="v1",
                params={
                    "min_confidence_to_trade": cfg.risk.min_confidence_to_trade,
                    "request_timeout_sec": cfg.reflex.request_timeout_sec,
                },
                status="paper_active",
                rationale="Initial baseline configuration",
                walk_forward_passed=True,
                paper_gate_passed=True,
            )

        self.market_data_source: Any = None
        if exchange is not None:
            self.exchange = exchange
            self.exchange_info = {"mode": exchange.mode, "fallback_used": False, "missing_piece": None}
        elif cfg.openmarket.enabled and om_token:
            from ase.openmarket_adapter import OpenMarketAdapter

            om = OpenMarketAdapter(
                token=om_token,
                workspace_id=cfg.openmarket.workspace_id,
                mcp_url=cfg.openmarket.mcp_url,
            )
            try:
                om.probe_connectivity()
                self.market_data_source = om
                if cfg.system.mode == "paper":
                    # OpenMarket blocks agent execution on its built-in Paper account (reason: venue_off);
                    # pair OpenMarket live feeds with local PaperExchangeAdapter ($10k ledger).
                    self.exchange, _ = create_exchange_adapter(
                        cfg, api_key=binance_key, api_secret=binance_secret
                    )
                    self.exchange_info = {
                        "mode": f"paper+openmarket_feed:{cfg.openmarket.workspace_id}",
                        "fallback_used": False,
                        "missing_piece": "OpenMarket tab paper account is unavailable to agents; using local PaperExchangeAdapter with live OpenMarket feeds",
                    }
                else:
                    self.exchange = om
                    self.exchange_info = {
                        "mode": f"openmarket:{cfg.openmarket.workspace_id}",
                        "fallback_used": False,
                        "missing_piece": None,
                    }
            except Exception as exc:
                self._log.warning("OpenMarket unreachable (%s); falling back to PaperExchangeAdapter", exc)
                self.exchange, self.exchange_info = create_exchange_adapter(
                    cfg, api_key=binance_key, api_secret=binance_secret
                )
        else:
            self.exchange, self.exchange_info = create_exchange_adapter(
                cfg, api_key=binance_key, api_secret=binance_secret
            )

        if reflex_provider is not None:
            provider = reflex_provider
        elif cfg.reflex.provider == "gemini_hydrogen" and gemini_key:
            provider = GeminiHydrogenReflexProvider(
                api_key=gemini_key,
                model_name=cfg.reflex.model,
                thinking_level=cfg.reflex.thinking_level,
                est_input_cost_per_1m_usd=cfg.reflex.est_input_cost_per_1m_usd,
                est_output_cost_per_1m_usd=cfg.reflex.est_output_cost_per_1m_usd,
            )
        else:
            allowed_map = {f.symbol: list(f.allowed_directions) for f in self.finalists}
            provider = DeterministicRuleReflex(allowed_directions_by_symbol=allowed_map)

        self.budget = DailyBudgetGuard(cfg.reflex.daily_budget_usd)
        self.reflex_runner = ReflexRunner(provider, self.budget)
        self.state_engine = StateEngine(
            max_tokens=cfg.reflex.max_snapshot_tokens,
            stale_feed_max_age_sec=cfg.exchange.stale_feed_max_age_sec,
            clock_skew_max_ms=cfg.exchange.clock_skew_max_ms,
        )
        self.risk_engine = RiskEngine(cfg.risk)

        saved_acct = self.store.get_runtime_value("account_state")
        if saved_acct:
            self.account = AccountState(**saved_acct)
        else:
            bal = self.exchange.get_balance_usdt()
            self.account = AccountState(equity=bal, peak_equity=bal, day_start_equity=bal)

        self.local_positions: dict[str, float] = self.store.get_runtime_value("local_positions", {})
        self.started_at = datetime.now(timezone.utc)
        self.last_cycle_ts: str | None = None

    def persist_state(self) -> None:
        self.store.set_runtime_value("account_state", self.account.__dict__)
        self.store.set_runtime_value("local_positions", self.local_positions)

    def health_payload(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        return {
            "status": "halted" if self.account.kill_switch_triggered else "ok",
            "mode": self.cfg.system.mode,
            "exchange_mode": self.exchange_info["mode"],
            "exchange_missing_piece": self.exchange_info["missing_piece"],
            "reflex_model": self.reflex_runner.provider.model_name,
            "uptime_sec": round((now - self.started_at).total_seconds(), 1),
            "last_cycle_ts": self.last_cycle_ts,
            "equity_usdt": round(self.account.equity, 2),
            "drawdown_pct": round(self.account.drawdown_pct, 4),
            "open_positions": self.account.open_positions,
            "kill_switch_triggered": self.account.kill_switch_triggered,
            "new_risk_paused": self.account.new_risk_paused,
            "daily_budget_spent_usd": round(self.budget.spent_usd(now), 4),
            "daily_budget_limit_usd": self.budget.daily_limit_usd,
        }

    def run_candle_cycle(
        self,
        *,
        as_of_ts: datetime,
        candles_by_symbol: dict[str, list[Candle]],
        orderbooks_by_symbol: dict[str, OrderBookTop],
        funding_by_symbol: dict[str, tuple[float, datetime]],
        local_now_ts: datetime | None = None,
        exchange_server_ts: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Process one completed 4h candle across all eligible finalists with full risk & reconciliation checks."""
        now = local_now_ts or as_of_ts
        exch_ts = exchange_server_ts or now
        events: list[dict[str, Any]] = []

        # 1. Reconcile local ledger vs exchange BEFORE any new order
        recon = reconcile_positions(self.local_positions, self.exchange.fetch_positions())
        if not recon.matched:
            self.account.new_risk_paused = True
            self.alerter.send("critical", f"Position reconciliation mismatch: {recon.mismatches}")
            self.persist_state()
            return [{"event": "reconciliation_mismatch", "mismatches": recon.mismatches}]

        btc_candles = candles_by_symbol.get("BTCUSDT", [])
        if len(btc_candles) < 21:
            return [{"event": "skipped", "reason": "insufficient_btc_candles"}]

        for fin in self.finalists:
            sym = fin.symbol
            if sym not in candles_by_symbol or sym not in orderbooks_by_symbol:
                continue
            fund_rate, fund_ts = funding_by_symbol.get(sym, (0.0, as_of_ts))
            try:
                snap = self.state_engine.build_snapshot(
                    symbol=sym,
                    as_of_ts=as_of_ts,
                    local_now_ts=now,
                    exchange_server_ts=exch_ts,
                    candles=candles_by_symbol[sym],
                    btc_candles=btc_candles,
                    orderbook=orderbooks_by_symbol[sym],
                    funding_rate=fund_rate,
                    funding_ts=fund_ts,
                    account_equity=self.account.equity,
                    peak_equity=self.account.peak_equity,
                    day_start_equity=self.account.day_start_equity,
                    gross_exposure_usd=self.account.gross_exposure_usd,
                    open_positions=self.account.open_positions,
                    daily_realized_pnl=self.account.equity - self.account.day_start_equity,
                )
            except Exception as exc:
                self.alerter.send("warning", f"State engine rejected {sym}: {exc}")
                self.store.log_decision(
                    ts=as_of_ts,
                    symbol=sym,
                    regime="high_vol",
                    direction="neutral",
                    setup_quality=0,
                    risk_state="reduce",
                    confidence=0.0,
                    action="hold",
                    executed=False,
                    skip_reason=f"state_error:{type(exc).__name__}",
                )
                events.append({"symbol": sym, "event": "state_rejected", "reason": str(exc)})
                continue

            outcome = self.reflex_runner.evaluate(snap)
            if outcome.skipped or outcome.decision is None:
                self.store.log_decision(
                    ts=as_of_ts,
                    symbol=sym,
                    regime="high_vol",
                    direction="neutral",
                    setup_quality=0,
                    risk_state="safe",
                    confidence=0.0,
                    action="hold",
                    executed=False,
                    skip_reason=outcome.skip_reason,
                )
                events.append({"symbol": sym, "event": "reflex_skipped", "reason": outcome.skip_reason})
                continue

            dec = outcome.decision
            fee_bps, slip_bps = self.cfg.costs.side_fee_and_slippage_bps(sym, fin.market_type)
            verdict = self.risk_engine.evaluate(
                decision=dec,
                account=self.account,
                price=snap.last_close,
                stop_loss_pct=fin.stop_loss_pct,
                fee_bps=fee_bps,
                slippage_bps=slip_bps,
                decision_count=self.store.decision_count(),
                baseline_brier=self.cfg.risk.baseline_brier_score,
            )

            if verdict.alert_telegram:
                self.alerter.send("alert", f"{sym}: {verdict.reason}")

            if not verdict.approved or dec.direction not in fin.allowed_directions:
                self.store.log_decision(
                    ts=as_of_ts,
                    symbol=sym,
                    regime=dec.regime,
                    direction=dec.direction,
                    setup_quality=dec.setup_quality,
                    risk_state=dec.risk_state,
                    confidence=dec.confidence,
                    action=dec.action,
                    executed=False,
                    skip_reason=None,
                )
                events.append({"symbol": sym, "event": "blocked_by_risk", "reason": verdict.reason})
                continue

            # Place order through verified exchange adapter
            side_str = "buy" if dec.direction == "long" else "sell"
            try:
                fill = self.exchange.place_order(
                    OrderRequest(
                        symbol=sym,
                        market_type=fin.market_type,
                        side=side_str,
                        quantity=verdict.quantity,
                        reference_price=snap.last_close,
                        ts=as_of_ts,
                    )
                )
                signed_qty = fill.quantity if side_str == "buy" else -fill.quantity
                self.local_positions[sym] = self.local_positions.get(sym, 0.0) + signed_qty
                self.account.open_positions = sum(1 for q in self.local_positions.values() if abs(q) > 1e-12)
                self.account.gross_exposure_usd += fill.quantity * fill.fill_price
                self.account.update_equity(self.exchange.get_balance_usdt())
                self.store.log_decision(
                    ts=as_of_ts,
                    symbol=sym,
                    regime=dec.regime,
                    direction=dec.direction,
                    setup_quality=dec.setup_quality,
                    risk_state=dec.risk_state,
                    confidence=dec.confidence,
                    action=dec.action,
                    executed=True,
                )
                events.append(
                    {
                        "symbol": sym,
                        "event": "filled",
                        "order_id": fill.order_id,
                        "side": side_str,
                        "qty": fill.quantity,
                        "fill_price": fill.fill_price,
                        "fee_usd": fill.fee_usd,
                    }
                )
            except Exception as exc:
                self.alerter.send("error", f"Order execution failed for {sym}: {exc}")
                events.append({"symbol": sym, "event": "order_failed", "reason": str(exc)})

        self.last_cycle_ts = as_of_ts.isoformat()
        self.persist_state()
        return events

    def poll_once(
        self,
        *,
        now: datetime | None = None,
        market_data_source: Any | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch latest completed 4h candles & quotes from active market source and run one cycle."""
        source = market_data_source or self.market_data_source
        if source is None:
            return [{"event": "idle", "reason": "no_market_data_source_configured"}]

        utc_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        btc_candles = source.fetch_4h_candles("BTCUSDT", "spot", as_of_ts=utc_now, limit=45)
        if not btc_candles:
            return [{"event": "skipped", "reason": "no_completed_btc_candles"}]

        as_of_ts = btc_candles[-1].close_ts
        candles_by_symbol: dict[str, list[Candle]] = {"BTCUSDT": btc_candles}
        orderbooks_by_symbol: dict[str, OrderBookTop] = {}
        funding_by_symbol: dict[str, tuple[float, datetime]] = {}

        for fin in self.finalists:
            try:
                bars = source.fetch_4h_candles(fin.symbol, fin.market_type, as_of_ts=as_of_ts, limit=45)
                if not bars:
                    continue
                ob = source.fetch_orderbook_top(
                    fin.symbol,
                    fin.market_type,
                    fallback_ts=utc_now,
                    fallback_candle=bars[-1],
                )
                candles_by_symbol[fin.symbol] = bars
                orderbooks_by_symbol[fin.symbol] = ob
                if hasattr(source, "fetch_latest_funding"):
                    funding_by_symbol[fin.symbol] = source.fetch_latest_funding(
                        fin.symbol, fin.market_type, as_of_ts=as_of_ts
                    )
                else:
                    funding_by_symbol[fin.symbol] = (0.0, as_of_ts)
            except Exception as exc:
                self._log.warning("Market feed fetch failed for %s: %s", fin.symbol, exc)

        clock_now = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
        exch_now = (
            source.server_time_utc()
            if now is None
            else clock_now
        )
        return self.run_candle_cycle(
            as_of_ts=as_of_ts,
            candles_by_symbol=candles_by_symbol,
            orderbooks_by_symbol=orderbooks_by_symbol,
            funding_by_symbol=funding_by_symbol,
            local_now_ts=clock_now,
            exchange_server_ts=exch_now,
        )

    def serve_forever(self, *, poll_interval_sec: int = 300) -> None:
        """Run health server and persistent 4h candle loop (for Docker Compose 24/7 container)."""
        health = HealthServer(
            host=self.cfg.system.health_host,
            port=self.cfg.system.health_port,
            status_fn=self.health_payload,
        )
        health.start()
        self._log.info(
            "All-Seeing-Eye engine started in mode=%s (exchange=%s) healthz=http://%s:%d/healthz",
            self.cfg.system.mode,
            self.exchange_info["mode"],
            self.cfg.system.health_host,
            health.port,
        )
        last_processed_slot: int | None = None
        try:
            while True:
                now = datetime.now(timezone.utc)
                current_4h_slot = int(now.timestamp() // (4 * 3600))
                if self.market_data_source is not None and current_4h_slot != last_processed_slot:
                    try:
                        self.poll_once(now=now)
                        last_processed_slot = current_4h_slot
                    except Exception as exc:
                        self._log.error("4h poll_once error: %s", exc)
                time.sleep(poll_interval_sec)
        except KeyboardInterrupt:
            pass
        finally:
            health.stop()
