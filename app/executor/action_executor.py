"""Action executor — main execution entry point (§8)."""

from __future__ import annotations

import dataclasses

from app.contracts import (
    Action,
    ExecutionState,
)
from app.core.recovery_case import RecoveryCase


@dataclasses.dataclass(frozen=True)
class ExecutionResult:
    """Canonical outcome of one executed action."""

    action: Action
    state: ExecutionState
    idempotency_key: str
    external_ref: str | None
    detail: str = ""


class ActionExecutor:
    """Executes a policy-approved action after reconciliation and idempotency
    checks (§8.1, §8.2, §8.3). Returns SUCCESS / FAILED / UNKNOWN — UNKNOWN is
    never blindly retried."""

    def execute(
        self, case: RecoveryCase, action: Action, payload: dict
    ) -> ExecutionResult:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §8")

    def execute_noop(self, case: RecoveryCase) -> ExecutionResult:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §6.1, §8")