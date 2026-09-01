"""Deterministic root-cause rules — level-1 of the §2.4 fallback hierarchy."""

from __future__ import annotations

import dataclasses

from app.contracts import RootCause
from app.core.recovery_case import RecoveryCase


@dataclasses.dataclass(frozen=True)
class RuleVerdict:
    """A single deterministic rule outcome."""

    name: str
    applies: bool
    root_cause: RootCause | None
    reason: str
    priority: int = 0


class RulesEngine:
    """Fast, explainable, rule-based classification covering the mandatory
    differentiators the plan forbids hiding behind ML:

      - terminal vs transient failures (§9.3)
      - mandate revoked by CUSTOMER (permanent stop) vs by BANK (re-auth ok)
      - dispute / fraud immediate-halt locks
    """

    def evaluate(self, case: RecoveryCase, event: dict) -> list[RuleVerdict]:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §4.1, §9.3")

    def classify_with_rules(
        self, case: RecoveryCase, event: dict
    ) -> RuleVerdict:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §2.4, §9.3")


def is_terminal_failure(error_source: str, error_description: str) -> bool:
    """Terminal failures end recovery; transient failures allow retry (§9.1, §9.3)."""
    raise NotImplementedError("TODO: ML track — see implementation_plan.md §9.3")


def mandate_revocation_direction(
    error_source: str, error_description: str
) -> str:
    """Return "CUSTOMER" | "BANK" | "UNKNOWN". The CUSTOMER direction is the
    #1 compliance trap — a permanent stop, never retried (§9.3)."""
    raise NotImplementedError("TODO: ML track — see implementation_plan.md §9.3")


def dispute_or_fraud_halt(case: RecoveryCase) -> RuleVerdict:
    """Immediate-halt rule for dispute/fraud (§7.1, §15.5 fraud/dispute rows)."""
    raise NotImplementedError("TODO: ML track — see implementation_plan.md §7.1")