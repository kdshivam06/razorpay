"""Master policy gate — AI proposes, policy gates, executor performs (§7.1)."""

from __future__ import annotations

import dataclasses

from app.contracts import (
    Action,
    PolicyGateResult,
)
from app.core.recovery_case import RecoveryCase


@dataclasses.dataclass(frozen=True)
class PolicyEvaluation:
    """Aggregate of every check the master gate ran for one (case, action)."""

    action: Action
    result: PolicyGateResult
    passed_checks: tuple[str, ...]
    failed_checks: tuple[str, ...]
    blocked_reasons: tuple[str, ...]
    violations: tuple[str, ...] = ()


class PolicyEngine:
    """The master gate evaluated for every outbound action (§7.1):

      consent | contact window | DND/preference | cooldown | fraud |
      dispute | RBAC | reversibility | blast radius | platform state
    """

    def evaluate(self, case: RecoveryCase, action: Action) -> PolicyEvaluation:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §7.1")

    def evaluate_autonomy(
        self, case: RecoveryCase, action: Action
    ) -> PolicyEvaluation:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §7.1, §7.7")

    def human_approval_required(
        self, case: RecoveryCase, action: Action
    ) -> bool:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §7.7")