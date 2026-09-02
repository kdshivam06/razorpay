"""Action executor — main execution entry point (§8).

Executes a policy-approved action after reconciliation and idempotency
checks (§8.1, §8.2, §8.3). Returns SUCCESS / FAILED / UNKNOWN.
"""

from __future__ import annotations

import dataclasses
import logging
import uuid

from app.contracts import (
    Action,
    ExecutionState,
    PolicyGateResult,
)
from app.core.recovery_case import RecoveryCase
from app.executor.idempotency import IdempotencyManager
from app.executor.transactional_outbox import TransactionalOutbox
from app.policy.policy_engine import PolicyEngine

logger = logging.getLogger(__name__)


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
    never blindly retried.
    """

    def __init__(
        self,
        policy_engine: PolicyEngine,
        idempotency_manager: IdempotencyManager | None = None,
        outbox: TransactionalOutbox | None = None,
    ) -> None:
        self._policy_engine = policy_engine
        self._idempotency = idempotency_manager or IdempotencyManager()
        self._outbox = outbox or TransactionalOutbox()

    def execute(
        self, case: RecoveryCase, action: Action, payload: dict
    ) -> ExecutionResult:
        """Execute an action, enforcing policy and idempotency."""
        logger.info("Executing action %s for case %s", action.value, case.case_id)

        # 1. Gate check: Policy Engine (§7.1)
        evaluation = self._policy_engine.evaluate(case, action)
        if evaluation.result == PolicyGateResult.BLOCKED:
            logger.warning(
                "Action %s blocked by policy: %s",
                action.value,
                evaluation.blocked_reasons,
            )
            return ExecutionResult(
                action=action,
                state=ExecutionState.FAILED,
                idempotency_key="",
                external_ref=None,
                detail=f"Policy Blocked: {evaluation.blocked_reasons}",
            )

        # 2. Reconcile state before execution (§8.2)
        if case.total_remaining() <= 0 and action not in {
            Action.NO_ACTION,
            Action.WAIT,
        }:
            logger.info(
                "Case %s is already paid. Cancelling action %s.",
                case.case_id,
                action.value,
            )
            return ExecutionResult(
                action=action,
                state=ExecutionState.FAILED,
                idempotency_key="",
                external_ref=None,
                detail="Reconciliation: case already paid",
            )

        # 3. Get idempotency key (§8.3)
        scope = f"{case.case_id}:{action.value}"
        idem_key, is_new = self._idempotency.get_or_create(scope, payload)

        if not is_new and self._idempotency.check(idem_key):
            logger.info("Idempotency hit for %s (key: %s)", scope, idem_key)
            return ExecutionResult(
                action=action,
                state=ExecutionState.SUCCESS,
                idempotency_key=idem_key,
                external_ref=None,
                detail="Idempotency cache hit",
            )

        # 4. Transactional Outbox write intent (§8.1)
        # Note: In a real app this is inside a DB transaction with case updates
        cmd = self._outbox.append(case.case_id, action, payload)

        # 5. External API Call (Mocked execution for now)
        try:
            # Here we would call Razorpay Python SDK, NotificationSender, etc.
            # E.g., if action == Action.SEND_PAYMENT_LINK: self._payment_links.create_or_reuse(...)

            # Mock success
            self._idempotency.mark_used(idem_key)
            return ExecutionResult(
                action=action,
                state=ExecutionState.SUCCESS,
                idempotency_key=idem_key,
                external_ref=cmd.command_id,
                detail="Successfully executed via outbox",
            )
        except Exception as e:
            logger.exception("Error executing %s", action.value)
            # Timeout / Unknown state -> UNKNOWN, never retry blindly
            return ExecutionResult(
                action=action,
                state=ExecutionState.UNKNOWN,
                idempotency_key=idem_key,
                external_ref=cmd.command_id,
                detail=str(e),
            )

    def execute_noop(self, case: RecoveryCase) -> ExecutionResult:
        """Executes a NO_ACTION or WAIT command."""
        return ExecutionResult(
            action=Action.NO_ACTION,
            state=ExecutionState.SUCCESS,
            idempotency_key=f"noop_{uuid.uuid4().hex[:8]}",
            external_ref=None,
            detail="No action taken",
        )
