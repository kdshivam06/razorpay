"""Action suppression — pre-execution suppression checks (§6.5, §13.5)."""

from __future__ import annotations

import dataclasses

from app.contracts import Action
from app.core.recovery_case import RecoveryCase


@dataclasses.dataclass(frozen=True)
class SuppressionCheck:
    """One suppression candidate and its verdict."""

    reason: str
    suppress: bool
    detail: str = ""


@dataclasses.dataclass(frozen=True)
class SuppressionVerdict:
    """Every suppression is logged — suppression is part of the value (§6.5)."""

    suppressed: bool
    checks: tuple[SuppressionCheck, ...]
    suppress_reasons: tuple[str, ...]


class ActionSuppressor:
    """Blocks an action before execution when any §6.5 condition holds:

    already paid / contacted recently / active link / human conversation /
    active PTP / disputed / opted out
    """

    def check(self, case: RecoveryCase, action: Action) -> SuppressionVerdict:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §6.5")

    def suppressed_actions(self, case: RecoveryCase) -> list[str]:
        raise NotImplementedError(
            "TODO: ML track — see implementation_plan.md §6.5, §13.5"
        )
