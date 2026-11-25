"""Nightly Self-Improvement Loop: review fills & misses, Brier score calibration, 1 versioned proposal, gated promotion & rollback."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ase.storage import PolicyVersion, StateStore


def compute_brier_score(samples: list[tuple[float, int]]) -> float:
    """Compute Brier calibration score: mean((predicted_prob - outcome)^2)."""
    if not samples:
        raise ValueError("Cannot compute Brier score on empty sample list")
    total = 0.0
    for prob, outcome in samples:
        p = min(max(float(prob), 0.0), 1.0)
        y = 1.0 if int(outcome) else 0.0
        total += (p - y) ** 2
    return total / len(samples)


@dataclass(frozen=True)
class NightlyReviewReport:
    as_of_ts: str
    fills_reviewed: int
    misses_reviewed: int
    brier_score: float | None
    baseline_brier: float
    proposed_changes: dict[str, Any]
    proposed_version: PolicyVersion | None
    auto_pushed_live: bool = False  # HARD INVARIANT: always False


class NightlyReviewer:
    """Reviews every fill and miss nightly, measures Brier calibration, and proposes at most ONE versioned change."""

    def __init__(self, store: StateStore, *, baseline_brier: float = 0.25) -> None:
        self.store = store
        self.baseline_brier = baseline_brier

    def _next_version_id(self) -> str:
        existing = self.store.list_policy_versions()
        return f"v{len(existing) + 1}"

    def run_review(self, *, as_of_ts: datetime | None = None) -> NightlyReviewReport:
        now = (as_of_ts or datetime.now(timezone.utc)).astimezone(timezone.utc)
        decisions = self.store.list_decisions(limit=500)

        fills = [d for d in decisions if d["executed"] == 1]
        misses = [d for d in decisions if d["skip_reason"] is not None]
        resolved = [
            (float(d["confidence"]), int(d["realized_outcome"]))
            for d in fills
            if d["realized_outcome"] is not None
        ]

        brier: float | None = compute_brier_score(resolved) if resolved else None
        active = self.store.get_active_policy()
        new_params = dict(active.params)
        proposed_changes: dict[str, Any] = {}
        proposed_ver: PolicyVersion | None = None

        # Propose STRICTLY ONE targeted change based on calibration / miss diagnostics
        if brier is not None and brier > self.baseline_brier:
            cur_conf = float(new_params.get("min_confidence_to_trade", 0.80))
            updated_conf = round(min(0.92, cur_conf + 0.02), 4)
            if updated_conf != cur_conf:
                proposed_changes["min_confidence_to_trade"] = updated_conf
                new_params["min_confidence_to_trade"] = updated_conf
                rationale = (
                    f"Brier score {brier:.4f} exceeded baseline {self.baseline_brier:.4f} "
                    f"across {len(resolved)} resolved fills; tighten min_confidence_to_trade "
                    f"from {cur_conf:.2f} to {updated_conf:.2f} pending walk-forward & paper gates."
                )
                proposed_ver = self.store.save_policy_version(
                    version=self._next_version_id(),
                    params=new_params,
                    status="proposed",
                    rationale=rationale,
                )
        elif len(misses) >= 3:
            cur_timeout = float(new_params.get("request_timeout_sec", 15.0))
            updated_timeout = min(25.0, cur_timeout + 3.0)
            if updated_timeout != cur_timeout:
                proposed_changes["request_timeout_sec"] = updated_timeout
                new_params["request_timeout_sec"] = updated_timeout
                rationale = (
                    f"Observed {len(misses)} missed/skipped candles; increase request_timeout_sec "
                    f"from {cur_timeout:.1f}s to {updated_timeout:.1f}s pending walk-forward & paper gates."
                )
                proposed_ver = self.store.save_policy_version(
                    version=self._next_version_id(),
                    params=new_params,
                    status="proposed",
                    rationale=rationale,
                )

        return NightlyReviewReport(
            as_of_ts=now.isoformat(),
            fills_reviewed=len(fills),
            misses_reviewed=len(misses),
            brier_score=brier,
            baseline_brier=self.baseline_brier,
            proposed_changes=proposed_changes,
            proposed_version=proposed_ver,
            auto_pushed_live=False,
        )

    def promote_proposal(
        self,
        version: str,
        *,
        walk_forward_passed: bool,
        paper_gate_passed: bool,
    ) -> PolicyVersion:
        """Promote a proposed policy to paper_active ONLY after passing walk-forward and paper gates."""
        if not walk_forward_passed or not paper_gate_passed:
            raise ValueError(
                f"Cannot promote {version}: requires walk_forward_passed=True AND paper_gate_passed=True"
            )
        target = self.store.get_policy_version(version)
        return self.store.save_policy_version(
            version=target.version,
            params=target.params,
            status="paper_active",
            rationale=target.rationale,
            walk_forward_passed=True,
            paper_gate_passed=True,
        )

    def rollback(self, target_version: str) -> PolicyVersion:
        """Roll back paper_active policy cleanly to a prior versioned snapshot."""
        target = self.store.get_policy_version(target_version)
        return self.store.save_policy_version(
            version=target.version,
            params=target.params,
            status="paper_active",
            rationale=f"Rollback to {target.version} (orig: {target.rationale})",
            walk_forward_passed=target.walk_forward_passed,
            paper_gate_passed=target.paper_gate_passed,
        )
