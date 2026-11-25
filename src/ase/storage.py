"""SQLite WAL durable state store for decisions, fills, policy versions, and budget audit."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PolicyVersion:
    version: str
    params: dict[str, Any]
    status: str  # proposed | paper_active | rolled_back | archived
    rationale: str
    created_at: str
    walk_forward_passed: bool = False
    paper_gate_passed: bool = False


class StateStore:
    """Crash-safe SQLite WAL persistence surviving container/host reboots."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    regime TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    setup_quality INTEGER NOT NULL,
                    risk_state TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    action TEXT NOT NULL,
                    executed INTEGER NOT NULL,
                    skip_reason TEXT,
                    realized_outcome INTEGER,
                    pnl_usd REAL NOT NULL DEFAULT 0.0
                );

                CREATE TABLE IF NOT EXISTS policy_versions (
                    version TEXT PRIMARY KEY,
                    params_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    walk_forward_passed INTEGER NOT NULL DEFAULT 0,
                    paper_gate_passed INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS runtime_state (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def log_decision(
        self,
        *,
        ts: datetime,
        symbol: str,
        regime: str,
        direction: str,
        setup_quality: int,
        risk_state: str,
        confidence: float,
        action: str,
        executed: bool,
        skip_reason: str | None = None,
        realized_outcome: int | None = None,
        pnl_usd: float = 0.0,
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO decisions
                (ts, symbol, regime, direction, setup_quality, risk_state, confidence,
                 action, executed, skip_reason, realized_outcome, pnl_usd)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts.astimezone(timezone.utc).isoformat(),
                    symbol,
                    regime,
                    direction,
                    int(setup_quality),
                    risk_state,
                    float(confidence),
                    action,
                    1 if executed else 0,
                    skip_reason,
                    realized_outcome,
                    float(pnl_usd),
                ),
            )
            return int(cur.lastrowid)

    def list_decisions(self, limit: int = 500) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def decision_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM decisions WHERE skip_reason IS NULL").fetchone()
            return int(row["cnt"])

    def save_policy_version(
        self,
        *,
        version: str,
        params: dict[str, Any],
        status: str,
        rationale: str,
        walk_forward_passed: bool = False,
        paper_gate_passed: bool = False,
    ) -> PolicyVersion:
        if status == "live":
            raise ValueError("Policy versions cannot be promoted directly to live via StateStore")
        created_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            if status == "paper_active":
                conn.execute(
                    "UPDATE policy_versions SET status = 'archived' WHERE status = 'paper_active'"
                )
            conn.execute(
                """
                INSERT OR REPLACE INTO policy_versions
                (version, params_json, status, rationale, created_at, walk_forward_passed, paper_gate_passed)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version,
                    json.dumps(params, sort_keys=True),
                    status,
                    rationale,
                    created_at,
                    1 if walk_forward_passed else 0,
                    1 if paper_gate_passed else 0,
                ),
            )
        return PolicyVersion(
            version=version,
            params=params,
            status=status,
            rationale=rationale,
            created_at=created_at,
            walk_forward_passed=walk_forward_passed,
            paper_gate_passed=paper_gate_passed,
        )

    def get_policy_version(self, version: str) -> PolicyVersion:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM policy_versions WHERE version = ?", (version,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Policy version not found: {version}")
            return self._row_to_policy(row)

    def get_active_policy(self) -> PolicyVersion:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM policy_versions WHERE status = 'paper_active' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if row is None:
                raise KeyError("No active policy version found")
            return self._row_to_policy(row)

    def list_policy_versions(self) -> list[PolicyVersion]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM policy_versions ORDER BY created_at ASC"
            ).fetchall()
            return [self._row_to_policy(r) for r in rows]

    def set_runtime_value(self, key: str, value: Any) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO runtime_state (key, value_json, updated_at) VALUES (?, ?, ?)",
                (key, json.dumps(value), now),
            )

    def get_runtime_value(self, key: str, default: Any = None) -> Any:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value_json FROM runtime_state WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                return default
            return json.loads(row["value_json"])

    @staticmethod
    def _row_to_policy(row: sqlite3.Row) -> PolicyVersion:
        return PolicyVersion(
            version=row["version"],
            params=json.loads(row["params_json"]),
            status=row["status"],
            rationale=row["rationale"],
            created_at=row["created_at"],
            walk_forward_passed=bool(row["walk_forward_passed"]),
            paper_gate_passed=bool(row["paper_gate_passed"]),
        )
