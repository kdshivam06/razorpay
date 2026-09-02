"""Reconciliation engine — periodic state reconciliation (§11.3).

Periodically reconciles:
  internal ledger vs Razorpay API vs webhooks vs payment links vs subscription state

Detects mismatches:
  - internal = pending, Razorpay = captured
  - link = paid, case = open
  - invoice = partially_paid, internal balance = wrong

Auto-repairs ONLY safe state transitions.
"""

from __future__ import annotations

import dataclasses
import logging
import time

from app.core.obligation import Obligation, ObligationStatus
from app.core.recovery_case import RecoveryCase
from app.reconciliation.out_of_order import OutOfOrderGuard

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class Mismatch:
    """One detected state mismatch between two truth sources."""

    case_id: str
    field: str
    internal_value: str
    external_value: str
    auto_repairable: bool
    severity: str = "MEDIUM"  # LOW / MEDIUM / HIGH / CRITICAL


@dataclasses.dataclass(frozen=True)
class RepairResult:
    """Outcome of attempting to auto-repair one mismatch."""

    mismatch: Mismatch
    repaired: bool
    detail: str


@dataclasses.dataclass(frozen=True)
class ReconciliationReport:
    """Outcome of a reconciliation sweep."""

    mismatches: tuple[Mismatch, ...]
    repairs: tuple[RepairResult, ...]
    repairable_count: int
    repaired_count: int
    sweep_ts: float


# Safe auto-repair transitions (from → to):
# These are state corrections that can never make things worse.
_SAFE_REPAIRS: dict[tuple[str, str], str] = {
    # internal says pending, external says captured → update to captured
    ("PENDING", "CAPTURED"): "forward_to_captured",
    ("PENDING", "SETTLED"): "forward_to_settled",
    ("OPEN", "RECOVERED"): "forward_to_recovered",
    ("OPEN", "PAID"): "close_case",
    # payment link mismatch
    ("CREATED", "PAID"): "link_paid",
    ("CREATED", "EXPIRED"): "link_expired",
    ("PARTIALLY_PAID", "PAID"): "link_fully_paid",
}


class ReconciliationEngine:
    """Periodically reconciles:

    internal ledger vs Razorpay API vs webhooks vs payment links vs
    subscription state (§11.1, §11.3). Auto-repairs ONLY safe forward
    transitions.
    """

    def __init__(self, out_of_order_guard: OutOfOrderGuard | None = None) -> None:
        self._oo_guard = out_of_order_guard or OutOfOrderGuard()
        self._last_sweep_ts: float = 0.0

    def reconcile_case(
        self,
        case: RecoveryCase,
        razorpay_state: dict | None = None,
        payment_link_states: list[dict] | None = None,
    ) -> ReconciliationReport:
        """Run reconciliation for a single case.

        Args:
            case: The internal RecoveryCase.
            razorpay_state: Latest state from Razorpay API for each
                obligation (e.g. {"pay_xyz": {"status": "captured", ...}}).
            payment_link_states: Latest payment link states from Razorpay.
        """
        mismatches: list[Mismatch] = []
        repairs: list[RepairResult] = []
        rz_state = razorpay_state or {}
        pl_states = payment_link_states or []

        # ── 1. Obligation status vs Razorpay payment status ──────
        for obligation in case.obligations:
            ext = rz_state.get(obligation.obligation_id, {})
            ext_status = ext.get("status", "").upper()

            if not ext_status:
                continue  # No external data to compare

            internal_status = obligation.status.value

            if internal_status != ext_status:
                is_safe = (internal_status, ext_status) in _SAFE_REPAIRS
                mismatch = Mismatch(
                    case_id=case.case_id,
                    field=f"obligation.{obligation.obligation_id}.status",
                    internal_value=internal_status,
                    external_value=ext_status,
                    auto_repairable=is_safe,
                    severity="HIGH" if not is_safe else "MEDIUM",
                )
                mismatches.append(mismatch)

                if is_safe:
                    repair = self._repair_obligation_status(obligation, ext_status)
                    repairs.append(
                        RepairResult(
                            mismatch=mismatch,
                            repaired=repair,
                            detail=(
                                f"auto-repaired {internal_status} → {ext_status}"
                                if repair
                                else f"repair failed for {internal_status} → {ext_status}"
                            ),
                        )
                    )

        # ── 2. Payment link status vs case state ─────────────────
        for pl_data in pl_states:
            pl_id = pl_data.get("id", "")
            pl_status = pl_data.get("status", "").upper()

            # Find matching internal link
            internal_link = None
            for link in case.payment_links:
                if link.get("id") == pl_id or link.get("link_id") == pl_id:
                    internal_link = link
                    break

            if internal_link is None:
                continue

            internal_status = internal_link.get("state", "UNKNOWN")

            if internal_status != pl_status:
                is_safe = (internal_status, pl_status) in _SAFE_REPAIRS
                mismatch = Mismatch(
                    case_id=case.case_id,
                    field=f"payment_link.{pl_id}.status",
                    internal_value=internal_status,
                    external_value=pl_status,
                    auto_repairable=is_safe,
                    severity="HIGH" if pl_status == "PAID" else "MEDIUM",
                )
                mismatches.append(mismatch)

        # ── 3. Case open but all obligations recovered ───────────
        if case.total_remaining() <= 0 and len(case.obligations) > 0:
            all_done = all(
                o.status
                in {
                    ObligationStatus.RECOVERED,
                    ObligationStatus.REFUNDED,
                }
                for o in case.obligations
            )
            if all_done and case.state.value not in {"RECOVERED", "CLOSED"}:
                mismatch = Mismatch(
                    case_id=case.case_id,
                    field="case.state",
                    internal_value=case.state.value,
                    external_value="RECOVERED",
                    auto_repairable=True,
                    severity="HIGH",
                )
                mismatches.append(mismatch)

        # ── 4. Balance mismatch ──────────────────────────────────
        for obligation in case.obligations:
            ext = rz_state.get(obligation.obligation_id, {})
            ext_amount_paid = ext.get("amount_paid", None)
            if ext_amount_paid is not None:
                internal_paid = obligation.amount_paise - obligation.remaining_amount
                if internal_paid != ext_amount_paid:
                    mismatch = Mismatch(
                        case_id=case.case_id,
                        field=f"obligation.{obligation.obligation_id}.amount_paid",
                        internal_value=str(internal_paid),
                        external_value=str(ext_amount_paid),
                        auto_repairable=False,
                        severity="CRITICAL",
                    )
                    mismatches.append(mismatch)

        sweep_ts = time.time()
        self._last_sweep_ts = sweep_ts

        repairable = sum(1 for m in mismatches if m.auto_repairable)
        repaired = sum(1 for r in repairs if r.repaired)

        if mismatches:
            logger.warning(
                "Reconciliation for case %s: %d mismatches (%d repairable, %d repaired)",
                case.case_id,
                len(mismatches),
                repairable,
                repaired,
            )
        else:
            logger.debug("Reconciliation for case %s: clean", case.case_id)

        return ReconciliationReport(
            mismatches=tuple(mismatches),
            repairs=tuple(repairs),
            repairable_count=repairable,
            repaired_count=repaired,
            sweep_ts=sweep_ts,
        )

    def reconcile_batch(
        self,
        cases: list[RecoveryCase],
        razorpay_states: dict[str, dict] | None = None,
    ) -> list[ReconciliationReport]:
        """Run reconciliation across a batch of cases."""
        rz_states = razorpay_states or {}
        reports = []
        for case in cases:
            rz = rz_states.get(case.case_id, {})
            report = self.reconcile_case(case, razorpay_state=rz)
            reports.append(report)
        return reports

    @staticmethod
    def _repair_obligation_status(obligation: Obligation, target: str) -> bool:
        """Attempt to auto-repair an obligation status (safe transitions only)."""
        try:
            target_status = ObligationStatus(target)
            obligation.status = target_status
            logger.info(
                "Auto-repaired obligation %s → %s",
                obligation.obligation_id,
                target,
            )
            return True
        except (ValueError, AttributeError):
            logger.error(
                "Failed to repair obligation %s → %s",
                obligation.obligation_id,
                target,
            )
            return False
