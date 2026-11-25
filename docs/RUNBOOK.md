# All-Seeing-Eye — Operations Runbook (`docs/RUNBOOK.md`)

**Scope:** 24/7 deployment, health monitoring, secret management, Telegram alerting, kill-switch recovery, rollback, and the Live Approval Gate.

---

## 1. Pre-Deployment Checklist
1. **Read `AGENTS.md` and `PROGRESS.md`** before making any config or policy change.
2. **Verify tests pass:**
   ```bash
   uv run pytest -q
   ```
3. **Provision external secrets OUTSIDE this repository** (e.g., `/etc/ase-secrets/`, mode `0700`, files mode `0600`):
   - `/etc/ase-secrets/gemini_api_key` — Gemini API key for Gemini 4 Hydrogen REFLEX
   - `/etc/ase-secrets/binance_api_key` — Binance Testnet (or Live, **only after approval gate**) API key
   - `/etc/ase-secrets/binance_api_secret` — Binance API secret
   - `/etc/ase-secrets/telegram_bot_token` — Optional Telegram bot token
   - `/etc/ase-secrets/telegram_chat_id` — Optional Telegram chat ID
4. **Exchange API Key Security Requirements (Mandatory before Testnet/Live):**
   - **Trading permission ONLY** (`Enable Spot & Margin Trading` / `Enable Futures`).
   - **Withdrawals STRICTLY DISABLED.**
   - **IP-whitelisted** to the static outbound IP of the 24/7 host server.
   - **Rotated every 90 days.**
   - Never stored inside this repository, `.env`, chat messages, or logs.

## 2. Launching the 24/7 Runtime (Docker Compose)
The Compose stack (`docker-compose.yml`) starts two services with `restart: unless-stopped`:
- `trading-engine`: 4h candle loop + HTTP `/healthz` endpoint on port `8080`.
- `nightly-review`: scheduled nightly calibration review, Brier scoring, and single-change proposal generator.

```bash
# Start in PAPER mode (default, safe without exchange credentials)
docker compose up -d --build

# Verify containers and Docker health status
docker compose ps
curl -fsS http://127.0.0.1:8080/healthz
```

**Surviving Reboots:** Ensure Docker daemon is enabled at boot (`sudo systemctl enable docker`). `restart: unless-stopped` brings both services back automatically and resumes from SQLite WAL state in the `ase_runtime` volume without losing open positions, drawdown high-water marks, or decision counts.

## 3. Exchange & OpenMarket Connectivity
- By default, `CcxtBinanceAdapter` targets Binance **Testnet first** (`sandbox_mode=True`).
- If the host IP is geo-restricted (`HTTP 451`, e.g., US-based cloud VMs) or exchange endpoints are unreachable, the engine automatically switches to `PaperExchangeAdapter` (when `fallback_to_paper_if_unreachable: true`), logs an explicit warning, and reports `"exchange_mode": "paper_fallback"` on `/healthz`.
- For real Binance Testnet or Live execution, deploy Docker Compose on an **exchange-eligible non-US server** with a static IP whitelisted on the Binance API key.
- **OpenMarket Chart `2op55llf` Paper Execution (Option A):**
  1. Generate a scoped Agent Token (`mcp:read mcp:chart mcp:trade`) at `https://openmarket.xyz/chart/console/agents` and store it at `/etc/ase-secrets/openmarket_token` (outside the repo, `chmod 600`).
  2. Set `openmarket.enabled: true` in `config/system.yaml` (workspace `2op55llf` is pre-configured).
  3. Open chart `2op55llf` in your browser on your **paper account**, search **"Agent trading"**, and arm the agent for the session.
  4. Start `uv run ase run --config config/system.yaml` (or `docker compose up -d --build`). The 4h loop polls completed bars, enforces all code-owned risk limits, and routes paper orders with deterministic `idempotency_key`s.

## 4. Incident Response & Kill Switch Recovery

| Alert / Condition | Automatic System Action | Operator Action |
|---|---|---|
| `confidence < 0.60` or `regime == "crisis"` | Pauses all **new** risk entries; maintains existing protective stops; sends Telegram alert. | Review market regime and macro feeds. Resume only when confidence recovers (`>= 0.60`) and regime exits `crisis`. |
| Daily loss `>= 5.0%` | Blocks all new entries until `00:00 UTC` next day. | Review fills/misses in nightly log. Do not widen stops or override cap. |
| Drawdown `>= 15.0%` (**Kill Switch**) | **Permanently halts trading engine**, cancels open entry orders, sends high-priority Telegram alert. **No automatic reset.** | Perform full post-mortem (`ase nightly --db .runtime/ase.db`), identify root cause, verify fixes via walk-forward + paper tests, and explicitly clear halt only with owner sign-off. |
| Stale feed / Clock skew `> 5s` / Exchange error | Skips current candle before any order; sends Telegram alert. | Check NTP (`chronyc tracking`), host network, and Binance status page. |
| Reconciliation mismatch | Halts new entries and alerts Telegram with local vs exchange position delta. | Manually reconcile positions on exchange UI vs SQLite ledger before resuming. |
| Daily LLM budget `$5.00` reached | Refuses additional REFLEX calls for remainder of UTC day; skips candles cleanly. | Inspect call frequency/token count in SQLite `reflex_calls` table. |

## 5. Policy Versioning & Rollback
Every nightly review creates at most **one** versioned policy proposal. Proposals NEVER go live automatically:
```bash
# Inspect policy versions and calibration Brier scores
uv run ase nightly --config config/system.yaml --db .runtime/ase.db

# Roll back to a known-good policy version
uv run ase rollback --db .runtime/ase.db --version v1
```

## 6. Live Trading Approval Gate (Mandatory)
Live execution is **hard-blocked in code** until ALL of the following are verified:
1. Strategy beats BTC buy-and-hold (or passes owner-accepted risk-adjusted criteria) in **post-March-2026 walk-forward backtests** with fees, slippage, and funding enabled.
2. Paper trading completes with **0 unhandled reconciliation errors**, verified Brier calibration, and owner review of at least 30 paper trades.
3. Trading-only, no-withdrawal, IP-whitelisted API keys are placed in `/etc/ase-secrets/` (outside repo).
4. Owner provides explicit written approval (`go live`) and sets:
   - `ASE_MODE=live`
   - `ASE_LIVE_APPROVED=true`
   - `ASE_LIVE_APPROVAL_TOKEN=<non-empty operator approval token>`
   - `exchange.testnet_first: false`
5. Validate gate before starting:
   ```bash
   uv run ase validate-gate --config config/system.yaml
   ```
