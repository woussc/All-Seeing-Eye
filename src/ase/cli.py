"""Command-line entrypoint for All-Seeing-Eye (paper, backtest, nightly, rollback, validate-gate)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ase.backtest import WalkForwardBacktester
from ase.config import assert_live_approval_gate, load_finalists, load_system_config
from ase.data_loader import load_universe_post_cutoff
from ase.engine import TradingEngine
from ase.nightly import NightlyReviewer
from ase.storage import StateStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ase", description="All-Seeing-Eye 24/7 Crypto Trading System")
    sub = parser.add_subparsers(dest="command", required=True)

    p_bt = sub.add_parser("backtest", help="Run post-March-2026 walk-forward backtest on Binance archives")
    p_bt.add_argument("--config", default="config/system.yaml")
    p_bt.add_argument("--finalists", default="config/finalists.yaml")
    p_bt.add_argument("--out", default=".runtime/backtest_report.json")

    p_run = sub.add_parser("run", help="Start 24/7 paper/testnet runtime with /healthz")
    p_run.add_argument("--config", default="config/system.yaml")
    p_run.add_argument("--finalists", default="config/finalists.yaml")
    p_run.add_argument("--poll-sec", type=int, default=300)

    p_night = sub.add_parser("nightly", help="Run nightly calibration review & single-change proposal")
    p_night.add_argument("--config", default="config/system.yaml")
    p_night.add_argument("--db", default=None)

    p_rb = sub.add_parser("rollback", help="Roll back active paper policy to a previous version")
    p_rb.add_argument("--db", default=".runtime/ase.db")
    p_rb.add_argument("--version", required=True)

    p_gate = sub.add_parser("validate-gate", help="Verify approval gate and external secret hygiene")
    p_gate.add_argument("--config", default="config/system.yaml")

    args = parser.parse_args(argv)

    if args.command == "validate-gate":
        cfg = load_system_config(args.config)
        assert_live_approval_gate(cfg)
        print(f"Gate OK for mode={cfg.system.mode} (testnet_first={cfg.exchange.testnet_first})")
        return 0

    if args.command == "backtest":
        cfg = load_system_config(args.config)
        finalists = load_finalists(args.finalists)
        pairs = [("BTCUSDT", "spot")] + [(f.symbol, f.market_type) for f in finalists]
        candles, funding = load_universe_post_cutoff(pairs)
        bt = WalkForwardBacktester(cfg, finalists)
        report = bt.run(candles_by_symbol=candles, funding_by_symbol=funding, uses_llm_decisions=True)
        print(report.format_summary())
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report.__dict__, default=lambda o: o.__dict__, indent=2), encoding="utf-8")
        print(f"\nSaved full JSON report to {out_path}")
        return 0

    if args.command == "nightly":
        cfg = load_system_config(args.config)
        store = StateStore(args.db or cfg.system.db_path)
        store.initialize()
        if not store.list_policy_versions():
            store.save_policy_version(
                version="v1",
                params={"min_confidence_to_trade": cfg.risk.min_confidence_to_trade},
                status="paper_active",
                rationale="Initial baseline",
                walk_forward_passed=True,
                paper_gate_passed=True,
            )
        reviewer = NightlyReviewer(store, baseline_brier=cfg.risk.baseline_brier_score)
        rep = reviewer.run_review()
        print(json.dumps(rep.__dict__, default=lambda o: o.__dict__, indent=2))
        return 0

    if args.command == "rollback":
        store = StateStore(args.db)
        store.initialize()
        reviewer = NightlyReviewer(store)
        ver = reviewer.rollback(args.version)
        print(f"Rolled back active paper policy to {ver.version}: {ver.params}")
        return 0

    if args.command == "run":
        cfg = load_system_config(args.config)
        finalists = load_finalists(args.finalists)
        engine = TradingEngine(cfg, finalists)
        engine.serve_forever(poll_interval_sec=args.poll_sec)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
