"""Typed configuration loader, off-limits universe filter, external secret guard, and live approval gate."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Mapping

import yaml
from pydantic import BaseModel, Field

from ase.schemas import FinalistSpec, MarketType


class SecretLocationError(RuntimeError):
    """Raised when a secret directory or key file resides inside the git repository."""


class LiveApprovalError(RuntimeError):
    """Raised when live trading is attempted without explicit owner approval."""


class SystemSection(BaseModel):
    name: str = "all-seeing-eye"
    mode: Literal["paper", "testnet", "live"] = "paper"
    timeframe: str = "4h"
    timezone_storage: str = "UTC"
    health_host: str = "0.0.0.0"
    health_port: int = 8080
    db_path: str = ".runtime/ase.db"
    secrets_dir: str = "/etc/ase-secrets"


class ExchangeSection(BaseModel):
    name: str = "binance"
    via: str = "ccxt"
    testnet_first: bool = True
    markets: list[MarketType] = Field(default_factory=lambda: ["spot", "linear_perps"])
    short_via_perps_only: bool = True
    fallback_to_paper_if_unreachable: bool = True
    clock_skew_max_ms: int = 5000
    stale_feed_max_age_sec: int = 900


class AccountSection(BaseModel):
    initial_paper_balance_usdt: float = 10000.0
    quote_currency: str = "USDT"


class OffLimitsSection(BaseModel):
    exclude_memecoins: bool = True
    exclude_leveraged_tokens: bool = True
    min_market_cap_usd: float = 200_000_000.0
    min_24h_volume_usd: float = 20_000_000.0
    blocked_symbols: list[str] = Field(default_factory=list)
    blocked_suffixes: list[str] = Field(
        default_factory=lambda: ["UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT", "3LUSDT", "3SUSDT"]
    )


class UniverseSection(BaseModel):
    off_limits: OffLimitsSection = Field(default_factory=OffLimitsSection)


class ReflexSection(BaseModel):
    provider: Literal["gemini_hydrogen", "deterministic"] = "deterministic"
    model: str = "gemini-4-hydrogen"
    thinking_level: str = "low"
    max_snapshot_tokens: int = 400
    daily_budget_usd: float = 5.0
    est_input_cost_per_1m_usd: float = 0.30
    est_output_cost_per_1m_usd: float = 1.20
    request_timeout_sec: float = 15.0
    skip_on_miss: bool = True
    knowledge_cutoff_utc: str = "2026-03-31T23:59:59Z"


class RiskSection(BaseModel):
    max_risk_per_trade_pct: float = 0.01
    max_daily_loss_pct: float = 0.05
    max_open_positions: int = 3
    max_leverage: float = 2.0
    kill_switch_drawdown_pct: float = 0.15
    min_setup_quality: int = 2
    min_confidence_to_trade: float = 0.80
    pause_confidence_threshold: float = 0.60
    fixed_min_decision_count: int = 200
    fixed_min_notional_pct: float = 0.05
    max_kelly_fraction: float = 0.25
    baseline_brier_score: float = 0.25


class CostsSection(BaseModel):
    spot_taker_fee_bps: float = 10.0
    perp_taker_fee_bps: float = 5.0
    major_half_spread_bps: float = 1.0
    major_slippage_bps: float = 5.0
    alt_half_spread_bps: float = 3.0
    alt_slippage_bps: float = 8.0

    def side_fee_and_slippage_bps(self, symbol: str, market_type: MarketType) -> tuple[float, float]:
        fee = self.spot_taker_fee_bps if market_type == "spot" else self.perp_taker_fee_bps
        is_major = symbol.upper() in {"BTCUSDT", "ETHUSDT"}
        spread = self.major_half_spread_bps if is_major else self.alt_half_spread_bps
        slip = self.major_slippage_bps if is_major else self.alt_slippage_bps
        return fee, spread + slip


class NightlySection(BaseModel):
    schedule_utc_hour: int = 0
    schedule_utc_minute: int = 15
    max_proposals_per_night: int = 1
    require_walk_forward_pass: bool = True
    require_paper_gate_pass: bool = True
    auto_push_live: bool = False


class OpenMarketSection(BaseModel):
    enabled: bool = False
    workspace_id: str = "2op55llf"
    mcp_url: str = "https://mcp.openmarket.xyz/"
    secret_name: str = "openmarket_token"


class SystemConfig(BaseModel):
    system: SystemSection = Field(default_factory=SystemSection)
    exchange: ExchangeSection = Field(default_factory=ExchangeSection)
    openmarket: OpenMarketSection = Field(default_factory=OpenMarketSection)
    account: AccountSection = Field(default_factory=AccountSection)
    universe: UniverseSection = Field(default_factory=UniverseSection)
    reflex: ReflexSection = Field(default_factory=ReflexSection)
    risk: RiskSection = Field(default_factory=RiskSection)
    costs: CostsSection = Field(default_factory=CostsSection)
    nightly: NightlySection = Field(default_factory=NightlySection)


def load_system_config(path: str | Path) -> SystemConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return SystemConfig.model_validate(raw)


def load_finalists(path: str | Path) -> list[FinalistSpec]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return [FinalistSpec.model_validate(item) for item in raw.get("finalists", [])]


def is_symbol_allowed(
    symbol: str,
    rules: OffLimitsSection,
    *,
    market_cap_usd: float | None = None,
    volume_24h_usd: float | None = None,
) -> tuple[bool, str]:
    """Enforce off-limits universe rules in code (no memes, no leveraged tokens, liquidity floor)."""
    sym = symbol.upper().replace("/", "").replace(":USDT", "")
    blocked = {s.upper() for s in rules.blocked_symbols}
    if sym in blocked:
        return False, f"{sym} is on the blocked off-limits list"
    if rules.exclude_leveraged_tokens:
        for suffix in rules.blocked_suffixes:
            if sym.endswith(suffix.upper()):
                return False, f"{sym} rejected as leveraged token (suffix {suffix})"
    if market_cap_usd is not None and market_cap_usd < rules.min_market_cap_usd:
        return (
            False,
            f"{sym} market cap ${market_cap_usd:,.0f} below floor ${rules.min_market_cap_usd:,.0f}",
        )
    if volume_24h_usd is not None and volume_24h_usd < rules.min_24h_volume_usd:
        return (
            False,
            f"{sym} 24h volume ${volume_24h_usd:,.0f} below floor ${rules.min_24h_volume_usd:,.0f}",
        )
    return True, "allowed"


def validate_secret_dir_outside_repo(secret_dir: str | Path, repo_root: Path | None = None) -> Path:
    """Refuse any secret path inside the git repository to prevent accidental key leaks."""
    resolved_secret = Path(secret_dir).expanduser().resolve()
    root = (repo_root or Path.cwd()).resolve()
    try:
        resolved_secret.relative_to(root)
    except ValueError:
        return resolved_secret
    raise SecretLocationError(
        f"Secret directory {resolved_secret} is inside repository {root}. "
        "Store trading keys and tokens OUTSIDE the repository (e.g., /etc/ase-secrets)."
    )


def read_external_secret(
    secret_name: str,
    secrets_dir: str | Path,
    *,
    env_fallback: str | None = None,
    repo_root: Path | None = None,
) -> str | None:
    """Read a secret from an external directory outside the repo (or env fallback)."""
    validated_dir = validate_secret_dir_outside_repo(secrets_dir, repo_root=repo_root)
    candidate = validated_dir / secret_name
    if candidate.is_file():
        value = candidate.read_text(encoding="utf-8").strip()
        return value or None
    if env_fallback:
        val = os.environ.get(env_fallback, "").strip()
        return val or None
    return None


def assert_live_approval_gate(cfg: SystemConfig, env: Mapping[str, str] | None = None) -> None:
    """Hard gate blocking live execution unless explicitly approved by the owner."""
    if cfg.system.mode != "live":
        return
    environ = env if env is not None else os.environ
    approved = environ.get("ASE_LIVE_APPROVED", "").strip().lower() == "true"
    token = environ.get("ASE_LIVE_APPROVAL_TOKEN", "").strip()
    if not approved or not token:
        raise LiveApprovalError(
            "Live trading is BLOCKED: explicit owner approval required "
            "(set ASE_LIVE_APPROVED=true and non-empty ASE_LIVE_APPROVAL_TOKEN)."
        )
    if cfg.exchange.testnet_first:
        raise LiveApprovalError(
            "Live trading is BLOCKED while exchange.testnet_first=true. "
            "Verify testnet and paper gates first, then explicitly disable testnet_first."
        )
