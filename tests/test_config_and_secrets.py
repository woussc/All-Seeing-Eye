"""Tests for config loading, off-limits filtering, secret hygiene, and live approval gate."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from ase.alerts import SecretRedactingFilter
from ase.config import (
    LiveApprovalError,
    SecretLocationError,
    assert_live_approval_gate,
    is_symbol_allowed,
    load_finalists,
    load_system_config,
    validate_secret_dir_outside_repo,
)


def test_load_default_configs() -> None:
    cfg = load_system_config("config/system.yaml")
    finalists = load_finalists("config/finalists.yaml")
    assert cfg.system.mode == "paper"
    assert cfg.account.initial_paper_balance_usdt == 10000.0
    assert cfg.system.timeframe == "4h"
    assert cfg.reflex.daily_budget_usd == 5.0
    assert len(finalists) == 7
    for fin in finalists:
        ok, reason = is_symbol_allowed(
            fin.symbol,
            cfg.universe.off_limits,
            market_cap_usd=fin.market_cap_usd,
            volume_24h_usd=fin.volume_24h_usd,
        )
        assert ok, f"{fin.symbol} should pass off-limits filter, got: {reason}"


def test_off_limits_rejects_memes_leveraged_and_illiquid() -> None:
    cfg = load_system_config("config/system.yaml")
    rules = cfg.universe.off_limits

    ok, reason = is_symbol_allowed("DOGEUSDT", rules, market_cap_usd=20e9, volume_24h_usd=2e9)
    assert not ok and "blocked" in reason.lower()

    ok, reason = is_symbol_allowed("BTCUPUSDT", rules, market_cap_usd=500e6, volume_24h_usd=50e6)
    assert not ok and "leveraged" in reason.lower()

    ok, reason = is_symbol_allowed("AEROUSDT", rules, market_cap_usd=100e6, volume_24h_usd=28e6)
    assert not ok

    ok, reason = is_symbol_allowed("SMALLUSDT", rules, market_cap_usd=150e6, volume_24h_usd=50e6)
    assert not ok and "market cap" in reason.lower()

    ok, reason = is_symbol_allowed("THINUSDT", rules, market_cap_usd=500e6, volume_24h_usd=5e6)
    assert not ok and "volume" in reason.lower()


def test_secret_dir_must_be_outside_repo(tmp_path: Path) -> None:
    repo_root = Path.cwd().resolve()
    inside = repo_root / "secrets"
    with pytest.raises(SecretLocationError):
        validate_secret_dir_outside_repo(inside, repo_root=repo_root)

    outside = tmp_path / "external-secrets"
    outside.mkdir()
    validated = validate_secret_dir_outside_repo(outside, repo_root=repo_root)
    assert validated == outside.resolve()


def test_secret_redacting_filter_never_prints_keys() -> None:
    secret_key = "BINANCE_SUPER_SECRET_KEY_987654321"
    tg_token = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
    filt = SecretRedactingFilter([secret_key, tg_token])

    record = logging.LogRecord(
        name="ase.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Connecting with key=%s and tg=%s",
        args=(secret_key, tg_token),
        exc_info=None,
    )
    filt.filter(record)
    rendered = record.getMessage()
    assert secret_key not in rendered
    assert tg_token not in rendered
    assert "[REDACTED]" in rendered


def test_live_approval_gate_blocks_without_explicit_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = load_system_config("config/system.yaml")
    # Paper mode passes cleanly
    assert_live_approval_gate(cfg, env={})

    # Live mode without explicit approval fails
    live_cfg = cfg.model_copy(deep=True)
    live_cfg.system.mode = "live"
    with pytest.raises(LiveApprovalError):
        assert_live_approval_gate(live_cfg, env={})

    with pytest.raises(LiveApprovalError):
        assert_live_approval_gate(
            live_cfg,
            env={"ASE_LIVE_APPROVED": "true", "ASE_LIVE_APPROVAL_TOKEN": ""},
        )

    # Even with env approval, testnet_first=True blocks accidental live switch
    with pytest.raises(LiveApprovalError):
        assert_live_approval_gate(
            live_cfg,
            env={"ASE_LIVE_APPROVED": "true", "ASE_LIVE_APPROVAL_TOKEN": "owner-approved-2026"},
        )

    # Explicit live config + approval passes
    live_cfg.exchange.testnet_first = False
    assert_live_approval_gate(
        live_cfg,
        env={"ASE_LIVE_APPROVED": "true", "ASE_LIVE_APPROVAL_TOKEN": "owner-approved-2026"},
    )
