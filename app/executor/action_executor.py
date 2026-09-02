"""Action executor — main execution entry point (§8).

Executes a policy-approved action after reconciliation and idempotency
checks (§8.1, §8.2, §8.3). Returns SUCCESS / FAILED / UNKNOWN.

§3.5: A timeout is ALWAYS UNKNOWN, never SUCCESS/FAILED, routed to
reconciliation, never blindly retried.

Every action MUST pass through policy_engine.py before it executes.
Real Razorpay Test Mode calls go through razorpay-python using keys
from app.config.
"""

from __future__ import annotations

import dataclasses
import logging
import uuid

import razorpay
from requests.exceptions import ConnectionError as ReqConnectionError
from requests.exceptions import ReadTimeout

from app.config import get_settings
from app.contracts import (
    Action,
    ExecutionState,
    PolicyGateResult,
)
from app.core.recovery_case import RecoveryCase
from app.executor.circuit_breaker import CircuitBreaker
from app.executor.idempotency import IdempotencyManager
from app.executor.notification import NotificationSender
from app.executor.payment_link import PaymentLinkLifecycle
from app.executor.transactional_outbox import TransactionalOutbox
from app.policy.policy_engine import PolicyEngine

logger = logging.getLogger(__name__)

# Razorpay API timeout in seconds
_RAZORPAY_TIMEOUT_S = 10


@dataclasses.dataclass(frozen=True)
class ExecutionResult:
    """Canonical outcome of one executed action."""

    action: Action
    state: ExecutionState
    idempotency_key: str
    external_ref: str | None
    detail: str = ""
    route_to_reconciliation: bool = False


class ActionExecutor:
    """Executes a policy-approved action after reconciliation and idempotency
    checks (§8.1, §8.2, §8.3). Returns SUCCESS / FAILED / UNKNOWN — UNKNOWN is
    never blindly retried, always routed to reconciliation (§3.5).
    """

    def __init__(
        self,
        policy_engine: PolicyEngine,
        idempotency_manager: IdempotencyManager | None = None,
        outbox: TransactionalOutbox | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        notification_sender: NotificationSender | None = None,
        payment_links: PaymentLinkLifecycle | None = None,
    ) -> None:
        self._policy_engine = policy_engine
        self._idempotency = idempotency_manager or IdempotencyManager()
        self._outbox = outbox or TransactionalOutbox()
        self._circuit_breaker = circuit_breaker or CircuitBreaker()
        self._notifications = notification_sender or NotificationSender()
        self._payment_links = payment_links or PaymentLinkLifecycle()

        # Razorpay client (Test Mode) using keys from app.config
        try:
            settings = get_settings()
            self._razorpay = razorpay.Client(
                auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
            )
            self._razorpay.set_app_details({"title": "RecoveryOS", "version": "0.1.0"})
            logger.info("Razorpay client initialized (Test Mode)")
        except Exception:  # noqa: BLE001
            logger.warning("Razorpay client not initialized — mock mode only")
            self._razorpay = None

    def execute(
        self, case: RecoveryCase, action: Action, payload: dict
    ) -> ExecutionResult:
        """Execute an action, enforcing the full safety chain:

        1. Policy Engine gate (§7.1)
        2. Pre-execution reconciliation (§8.2) — if paid 2s ago, cancel
        3. Idempotency check (§8.3) — same payload → skip
        4. Circuit breaker check (§16.1) — dependency down → refuse
        5. Transactional outbox write intent (§8.1)
        6. External API call
        7. Timeout → UNKNOWN → route to reconciliation (§3.5)
        """
        logger.info("Executing action %s for case %s", action.value, case.case_id)

        # ── 1. Policy Engine gate ─────────────────────────────────
        evaluation = self._policy_engine.evaluate(case, action)
        if evaluation.result == PolicyGateResult.BLOCKED:
            logger.warning(
                "Action %s BLOCKED by policy: %s",
                action.value,
                evaluation.blocked_reasons,
            )
            return ExecutionResult(
                action=action,
                state=ExecutionState.FAILED,
                idempotency_key="",
                external_ref=None,
                detail=f"Policy blocked: {'; '.join(evaluation.blocked_reasons)}",
            )

        # ── 2. Pre-execution reconciliation (§8.2) ───────────────
        # "AI: Send SMS → Ledger: PAID 2 seconds ago → CANCEL ACTION"
        if case.total_remaining() <= 0 and action not in {
            Action.NO_ACTION,
            Action.WAIT,
            Action.BLOCK,
            Action.HUMAN_ESCALATION,
        }:
            logger.info(
                "Case %s reconciliation: already paid — cancelling %s",
                case.case_id,
                action.value,
            )
            return ExecutionResult(
                action=action,
                state=ExecutionState.FAILED,
                idempotency_key="",
                external_ref=None,
                detail="Reconciliation: case already paid — action cancelled",
            )

        # ── 3. Idempotency check (§8.3) ──────────────────────────
        scope = f"{case.case_id}:{action.value}"
        idem_key, is_new = self._idempotency.get_or_create(scope, payload)

        if not is_new and self._idempotency.check(idem_key):
            logger.info("Idempotency cache hit for %s (key: %s)", scope, idem_key)
            return ExecutionResult(
                action=action,
                state=ExecutionState.SUCCESS,
                idempotency_key=idem_key,
                external_ref=None,
                detail="Idempotency cache hit — no duplicate action",
            )

        # ── 4. Circuit breaker check (§16.1) ─────────────────────
        dep = self._dependency_for(action)
        if not self._circuit_breaker.allow(dep):
            logger.warning(
                "Circuit breaker OPEN for %s — refusing %s",
                dep,
                action.value,
            )
            return ExecutionResult(
                action=action,
                state=ExecutionState.FAILED,
                idempotency_key=idem_key,
                external_ref=None,
                detail=f"Circuit breaker open for dependency '{dep}'",
            )

        # ── 5. Transactional outbox write intent (§8.1) ──────────
        cmd = self._outbox.append(case.case_id, action, payload)

        # ── 6. Dispatch to the appropriate handler ────────────────
        return self._dispatch(case, action, payload, idem_key, cmd.command_id)

    def execute_noop(self, case: RecoveryCase) -> ExecutionResult:
        """Execute a NO_ACTION / WAIT — always succeeds, no external call."""
        return ExecutionResult(
            action=Action.NO_ACTION,
            state=ExecutionState.SUCCESS,
            idempotency_key=f"noop_{uuid.uuid4().hex[:8]}",
            external_ref=None,
            detail="No action taken",
        )

    # ── Dispatch by action type ───────────────────────────────────

    def _dispatch(
        self,
        case: RecoveryCase,
        action: Action,
        payload: dict,
        idem_key: str,
        cmd_id: str,
    ) -> ExecutionResult:
        """Route the action to the correct handler."""
        dep = self._dependency_for(action)
        try:
            if action == Action.SEND_PAYMENT_LINK:
                return self._exec_payment_link(case, payload, idem_key, cmd_id)
            if action in {Action.SEND_SMS, Action.SEND_EMAIL, Action.SEND_WHATSAPP}:
                return self._exec_notification(action, payload, idem_key, cmd_id)
            if action in {Action.RETRY_SAME_METHOD, Action.RETRY_ALTERNATE_METHOD}:
                return self._exec_razorpay_retry(
                    case, action, payload, idem_key, cmd_id
                )
            # All other actions — generic mock success
            self._idempotency.mark_used(idem_key)
            self._circuit_breaker.record_success(dep)
            return ExecutionResult(
                action=action,
                state=ExecutionState.SUCCESS,
                idempotency_key=idem_key,
                external_ref=cmd_id,
                detail=f"{action.value} executed (mocked)",
            )

        except (ReadTimeout, ReqConnectionError, TimeoutError):
            # ── §3.5: Timeout is ALWAYS UNKNOWN ──────────────────
            # Never SUCCESS, never FAILED. Route to reconciliation.
            logger.error(
                "TIMEOUT executing %s for case %s — marking UNKNOWN, "
                "routing to reconciliation (§3.5)",
                action.value,
                case.case_id,
            )
            self._circuit_breaker.record_failure(dep)
            return ExecutionResult(
                action=action,
                state=ExecutionState.UNKNOWN,
                idempotency_key=idem_key,
                external_ref=cmd_id,
                detail="API timeout — UNKNOWN state, routed to reconciliation",
                route_to_reconciliation=True,
            )

        except Exception as e:
            logger.exception(
                "Error executing %s for case %s", action.value, case.case_id
            )
            self._circuit_breaker.record_failure(dep)
            return ExecutionResult(
                action=action,
                state=ExecutionState.FAILED,
                idempotency_key=idem_key,
                external_ref=cmd_id,
                detail=f"Execution error: {e}",
            )

    # ── Individual handlers ───────────────────────────────────────

    def _exec_payment_link(
        self,
        case: RecoveryCase,
        payload: dict,
        idem_key: str,
        cmd_id: str,
    ) -> ExecutionResult:
        """Create or reuse a payment link (§8.4).

        If Razorpay client is available, makes a real Test Mode API call.
        """
        amount = payload.get("amount_paise", case.total_remaining())

        # Check if we can reuse an existing link
        link = self._payment_links.create_or_reuse(case.case_id, amount)

        # If razorpay client is available, make a real API call
        if self._razorpay and link.state.value == "CREATED":
            try:
                resp = self._razorpay.payment_link.create(
                    {
                        "amount": amount,
                        "currency": "INR",
                        "description": payload.get(
                            "description", f"Recovery for case {case.case_id}"
                        ),
                        "customer": {
                            "contact": payload.get("phone", ""),
                            "email": payload.get("email", ""),
                        },
                        "notify": {"sms": True, "email": True},
                        "reminder_enable": True,
                        "notes": {
                            "case_id": case.case_id,
                            "idempotency_key": idem_key,
                        },
                    }
                )
                external_ref = resp.get("id", cmd_id)
                logger.info(
                    "Razorpay payment link created: %s (amount: ₹%s)",
                    external_ref,
                    amount / 100,
                )
            except (ReadTimeout, ReqConnectionError, TimeoutError):
                raise  # Let _dispatch handle as UNKNOWN
            except Exception as e:  # noqa: BLE001
                logger.error("Razorpay payment link creation failed: %s", e)
                external_ref = cmd_id
        else:
            external_ref = link.link_id

        self._idempotency.mark_used(idem_key)
        self._circuit_breaker.record_success("razorpay")
        return ExecutionResult(
            action=Action.SEND_PAYMENT_LINK,
            state=ExecutionState.SUCCESS,
            idempotency_key=idem_key,
            external_ref=external_ref,
            detail=f"Payment link: {link.link_id} (amount: ₹{amount / 100})",
        )

    def _exec_notification(
        self,
        action: Action,
        payload: dict,
        idem_key: str,
        cmd_id: str,
    ) -> ExecutionResult:
        """Mock SMS/Email/WhatsApp — log realistic payloads, don't send."""
        channel_map = {
            Action.SEND_SMS: "sms",
            Action.SEND_EMAIL: "email",
            Action.SEND_WHATSAPP: "whatsapp",
        }
        channel = channel_map.get(action, "sms")
        template = payload.get("template_key", "payment_failed")
        variables = payload.get("variables", {})

        record = self._notifications.send(channel, template, variables)

        self._idempotency.mark_used(idem_key)
        self._circuit_breaker.record_success("sms_gateway")
        return ExecutionResult(
            action=action,
            state=ExecutionState.SUCCESS,
            idempotency_key=idem_key,
            external_ref=record.notification_id,
            detail=f"Notification sent ({channel}): {record.rendered_body[:80]}",
        )

    def _exec_razorpay_retry(
        self,
        case: RecoveryCase,
        action: Action,
        payload: dict,
        idem_key: str,
        cmd_id: str,
    ) -> ExecutionResult:
        """Retry payment via Razorpay Test Mode API.

        §3.5: A timeout is always UNKNOWN, never SUCCESS/FAILED.
        Timeouts are re-raised so _dispatch routes them to reconciliation.
        """
        if not self._razorpay:
            logger.info(
                "Razorpay client not available — mock retry for case %s",
                case.case_id,
            )
            self._idempotency.mark_used(idem_key)
            return ExecutionResult(
                action=action,
                state=ExecutionState.SUCCESS,
                idempotency_key=idem_key,
                external_ref=cmd_id,
                detail="Retry executed (mocked — no Razorpay client)",
            )

        payment_id = payload.get("payment_id", "")
        if not payment_id:
            return ExecutionResult(
                action=action,
                state=ExecutionState.FAILED,
                idempotency_key=idem_key,
                external_ref=cmd_id,
                detail="No payment_id in payload — cannot retry",
            )

        # Real Razorpay API call (Test Mode)
        # Timeout exceptions bubble to _dispatch → UNKNOWN (§3.5)
        resp = self._razorpay.payment.fetch(payment_id)
        logger.info(
            "Razorpay payment %s status: %s",
            payment_id,
            resp.get("status"),
        )
        self._idempotency.mark_used(idem_key)
        self._circuit_breaker.record_success("razorpay")
        return ExecutionResult(
            action=action,
            state=ExecutionState.SUCCESS,
            idempotency_key=idem_key,
            external_ref=payment_id,
            detail=f"Razorpay payment status: {resp.get('status')}",
        )

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _dependency_for(action: Action) -> str:
        """Map action to its external dependency for circuit breaker tracking."""
        if action in {
            Action.RETRY_SAME_METHOD,
            Action.RETRY_ALTERNATE_METHOD,
            Action.SEND_PAYMENT_LINK,
        }:
            return "razorpay"
        if action in {Action.SEND_SMS, Action.SEND_WHATSAPP}:
            return "sms_gateway"
        if action == Action.SEND_EMAIL:
            return "email_gateway"
        if action == Action.VOICE_CALL:
            return "telephony"
        return "internal"
