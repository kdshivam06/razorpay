"""UNKNOWN API-result reconciliation (§8.3, §3.5).

When `Create Payment Link → API timeout`:
  Do NOT retry create immediately.
  → UNKNOWN
  → search/reconcile existing Payment Links
  → same idempotency key?
  → existing link found?

This prevents duplicate external effects.
"""

from __future__ import annotations

import dataclasses
import logging

from app.contracts import Action, ExecutionState

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class UnknownResolution:
    """Resolution of an UNKNOWN outbound action result."""

    action: Action
    resolved: bool
    actual_state: ExecutionState
    duplicate_effect_found: bool
    external_ref: str | None
    recommendation: str


class UnknownStateHandler:
    """Because 'Create Payment Link → timeout' means UNKNOWN, not FAILURE.

    On UNKNOWN:
      1. Search/reconcile existing resources (payment links, payments)
      2. Match by idempotency key
      3. If found → mark SUCCESS, no retry
      4. If not found → safe to retry with same idempotency key
      5. NEVER blind-retry (§8.3, §3.5)

    §3.5: A timeout is ALWAYS UNKNOWN, never SUCCESS/FAILED,
    routed to reconciliation, never blindly retried.
    """

    def __init__(self) -> None:
        # Track UNKNOWN actions pending resolution
        # case_id → list of (action, idempotency_key, timestamp)
        self._pending: dict[str, list[dict]] = {}

    def register_unknown(
        self,
        case_id: str,
        action: Action,
        idempotency_key: str,
        external_ref: str | None = None,
    ) -> None:
        """Register an UNKNOWN result for later reconciliation."""
        entry = {
            "action": action,
            "idempotency_key": idempotency_key,
            "external_ref": external_ref,
            "resolved": False,
        }
        self._pending.setdefault(case_id, []).append(entry)
        logger.info(
            "Registered UNKNOWN result for case %s: %s (key: %s)",
            case_id,
            action.value,
            idempotency_key,
        )

    def resolve_unknown(
        self,
        case_id: str,
        action: Action,
        idempotency_key: str,
        existing_resources: list[dict] | None = None,
    ) -> UnknownResolution:
        """Attempt to resolve an UNKNOWN result.

        Steps:
          1. Search existing resources (payment links, payments) for a match
          2. Match by idempotency key in notes/metadata
          3. If found → SUCCESS (the action went through despite timeout)
          4. If not found → safe to retry with the SAME idempotency key

        Args:
            case_id: The recovery case ID.
            action: The action that timed out.
            idempotency_key: The idempotency key used for the original request.
            existing_resources: List of resources fetched from Razorpay API
                (payment links, payments, etc.) to search for duplicates.
        """
        resources = existing_resources or []

        # ── Step 1: Search by idempotency key ─────────────────────
        for resource in resources:
            notes = resource.get("notes", {})
            if notes.get("idempotency_key") == idempotency_key:
                # Found it — the action DID go through
                external_ref = resource.get("id", "")
                logger.info(
                    "UNKNOWN resolved → SUCCESS: found resource %s "
                    "with matching idempotency key %s for case %s",
                    external_ref,
                    idempotency_key,
                    case_id,
                )
                self._mark_resolved(case_id, idempotency_key)
                return UnknownResolution(
                    action=action,
                    resolved=True,
                    actual_state=ExecutionState.SUCCESS,
                    duplicate_effect_found=True,
                    external_ref=external_ref,
                    recommendation="action already completed — do not retry",
                )

        # ── Step 2: Search by amount/case_id match ────────────────
        for resource in resources:
            notes = resource.get("notes", {})
            if notes.get("case_id") == case_id:
                # Possible match by case context (less certain)
                resource_status = resource.get("status", "")
                if resource_status in {"created", "issued", "active"}:
                    external_ref = resource.get("id", "")
                    logger.info(
                        "UNKNOWN resolved → LIKELY SUCCESS: found resource %s "
                        "matching case %s (status: %s). Treating as success.",
                        external_ref,
                        case_id,
                        resource_status,
                    )
                    self._mark_resolved(case_id, idempotency_key)
                    return UnknownResolution(
                        action=action,
                        resolved=True,
                        actual_state=ExecutionState.SUCCESS,
                        duplicate_effect_found=True,
                        external_ref=external_ref,
                        recommendation=(
                            f"likely duplicate found ({external_ref}) — do not retry"
                        ),
                    )

        # ── Step 3: Not found — safe to retry ─────────────────────
        logger.info(
            "UNKNOWN not resolved for case %s action %s: no matching resource "
            "found. Safe to retry with same idempotency key %s.",
            case_id,
            action.value,
            idempotency_key,
        )
        return UnknownResolution(
            action=action,
            resolved=False,
            actual_state=ExecutionState.UNKNOWN,
            duplicate_effect_found=False,
            external_ref=None,
            recommendation=(
                f"no duplicate found — safe to retry with "
                f"idempotency key {idempotency_key}"
            ),
        )

    def pending_unknowns(self, case_id: str) -> list[dict]:
        """Get all unresolved UNKNOWN results for a case."""
        return [
            entry for entry in self._pending.get(case_id, []) if not entry["resolved"]
        ]

    def _mark_resolved(self, case_id: str, idempotency_key: str) -> None:
        """Mark an UNKNOWN entry as resolved."""
        for entry in self._pending.get(case_id, []):
            if entry["idempotency_key"] == idempotency_key:
                entry["resolved"] = True
                break
