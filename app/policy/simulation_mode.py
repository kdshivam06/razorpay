"""Policy simulation mode — SIMULATE before EXECUTE (§7.8).

Runs the full proposed pipeline in SIMULATE mode: all policy checks, all
economics, all suppression — but NO external action happens.  This gives
judges an interactive demo and makes the system safer.

Workflow:
  1. SIMULATE → report (predicted recovery, cost, violations, human reviews)
  2. Review report
  3. EXECUTE RECOVERY (only after explicit approval)
"""

from __future__ import annotations

import dataclasses
from collections import defaultdict

from app.contracts import Action


@dataclasses.dataclass(frozen=True)
class SimulationReport:
    """Policy/optimizer feasibility report from §7.8. No external action yet."""

    cases_evaluated: int
    revenue_at_risk_paise: int
    expected_natural_recovery_paise: int
    expected_incremental_recovery_paise: int
    action_cost_paise: int
    compliance_violations: int
    human_reviews: int
    proposed_actions: dict[Action, int]
    suppressed_actions: dict[Action, int] = dataclasses.field(default_factory=dict)
    policy_blocked_count: int = 0
    estimated_roi: float = 0.0


class SimulationMode:
    """Runs the full proposed pipeline in SIMULATE and ONLY THEN EXECUTE (§7.8).

    This is what makes the interactive judge demo safe — until the report is
    reviewed, no external action happens.
    """

    def __init__(self) -> None:
        self._last_report: SimulationReport | None = None
        self._execution_approved: bool = False

    def simulate(
        self,
        cases: list[dict],
        strategy_name: str = "default",
        *,
        execute_after: bool = False,
    ) -> SimulationReport:
        """Run the full pipeline in simulation mode.

        Each entry in ``cases`` should contain:
          - case_id: str
          - revenue_at_risk_paise: int
          - natural_recovery_paise: int
          - incremental_recovery_paise: int
          - action_cost_paise: int
          - proposed_action: str (Action value)
          - policy_blocked: bool (optional)
          - compliance_violation: bool (optional)
          - human_review_required: bool (optional)
          - suppressed_action: str (optional)
        """
        total_risk = 0
        total_natural = 0
        total_incremental = 0
        total_cost = 0
        violations = 0
        human_reviews = 0
        policy_blocked = 0
        proposed: dict[Action, int] = defaultdict(int)
        suppressed: dict[Action, int] = defaultdict(int)

        for entry in cases:
            total_risk += int(entry.get("revenue_at_risk_paise", 0))
            total_natural += int(entry.get("natural_recovery_paise", 0))
            total_incremental += int(entry.get("incremental_recovery_paise", 0))
            total_cost += int(entry.get("action_cost_paise", 0))

            if entry.get("compliance_violation"):
                violations += 1

            if entry.get("human_review_required"):
                human_reviews += 1

            if entry.get("policy_blocked"):
                policy_blocked += 1

            # Track proposed actions
            action_str = entry.get("proposed_action", "NO_ACTION")
            try:
                action = Action(action_str)
            except ValueError:
                action = Action.NO_ACTION
            proposed[action] += 1

            # Track suppressed actions
            suppressed_str = entry.get("suppressed_action")
            if suppressed_str:
                try:
                    supp_action = Action(suppressed_str)
                    suppressed[supp_action] += 1
                except ValueError:
                    pass

        # Compute ROI
        roi = 0.0
        if total_cost > 0:
            roi = round(total_incremental / total_cost, 2)

        report = SimulationReport(
            cases_evaluated=len(cases),
            revenue_at_risk_paise=total_risk,
            expected_natural_recovery_paise=total_natural,
            expected_incremental_recovery_paise=total_incremental,
            action_cost_paise=total_cost,
            compliance_violations=violations,
            human_reviews=human_reviews,
            proposed_actions=dict(proposed),
            suppressed_actions=dict(suppressed),
            policy_blocked_count=policy_blocked,
            estimated_roi=roi,
        )

        self._last_report = report
        self._execution_approved = execute_after

        return report

    @property
    def last_report(self) -> SimulationReport | None:
        return self._last_report

    @property
    def execution_approved(self) -> bool:
        return self._execution_approved

    def approve_execution(self) -> None:
        """Explicitly approve execution after reviewing the simulation report."""
        if self._last_report is None:
            raise RuntimeError("No simulation has been run — cannot approve execution")
        self._execution_approved = True

    def reject_execution(self) -> None:
        """Reject execution — no actions will be taken."""
        self._execution_approved = False

    def format_report(self, report: SimulationReport | None = None) -> str:
        """Format the simulation report as a human-readable summary."""
        r = report or self._last_report
        if r is None:
            return "No simulation report available."

        lines = [
            "=== SIMULATION REPORT ===",
            f"Cases evaluated: {r.cases_evaluated}",
            f"Revenue at risk: ₹{r.revenue_at_risk_paise / 100:,.0f}",
            f"Expected natural recovery: ₹{r.expected_natural_recovery_paise / 100:,.0f}",
            f"Expected incremental recovery: ₹{r.expected_incremental_recovery_paise / 100:,.0f}",
            f"Action cost: ₹{r.action_cost_paise / 100:,.0f}",
            f"Estimated ROI: {r.estimated_roi:.1f}x",
            f"Compliance violations: {r.compliance_violations}",
            f"Human reviews required: {r.human_reviews}",
            f"Policy-blocked actions: {r.policy_blocked_count}",
            "",
            "Proposed actions:",
        ]
        for action, count in sorted(r.proposed_actions.items(), key=lambda x: -x[1]):
            lines.append(f"  {action.value}: {count}")

        if r.suppressed_actions:
            lines.append("")
            lines.append("Suppressed actions:")
            for action, count in sorted(
                r.suppressed_actions.items(), key=lambda x: -x[1]
            ):
                lines.append(f"  {action.value}: {count}")

        lines.append("")
        lines.append("No external action has been taken.")

        return "\n".join(lines)
