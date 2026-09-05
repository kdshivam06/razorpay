"""Legal basis registry for policy gates.

Maps each gate_name to the specific rule/regulation it enforces.
Where a gate is an internal risk policy rather than a statutory requirement,
this is honestly noted — no invented legal citations.
"""

from __future__ import annotations

# Gate name -> (legal_basis, description)
# legal_basis: short citation or "internal_policy"
# description: human-readable explanation

GATE_LEGAL_BASIS: dict[str, tuple[str, str]] = {
    # Statutory / regulatory gates
    "consent": (
        "TRAI Telecom Commercial Communications Customer Preference Regulations (TCCCPR), 2018",
        ("TRAI DLT registration & explicit consent required for commercial SMS/voice. "
        "Consent must be specific to purpose (payment_recovery), scoped, timestamped, and revocable.")
    ),
    "contact_window": (
        ("RBI Master Direction — Reserve Bank of India (Regulatory Framework for Recovery Agents), 2023; "
        "TRAI TCCCPR 2018"),
        ("Contact hours for recovery communications. Voice: 08:00–19:00 (RBI recovery agent rule). "
        "SMS/Email/WhatsApp: configurable per merchant/customer preference (TRAI permits wider windows). "
        "Do NOT hard-code RBI 8–7 PM for all channels.")
    ),

    # Platform / contract gates (not statutory)
    "customer_preference": (
        "internal_policy",
        ("Customer-stated DND/channel/time preferences. Enforced as: "
        "legal_window ∩ merchant_policy ∩ customer_preference ∩ channel_availability. "
        "Not a statutory rule — merchant/customer contract terms.")
    ),
    "cooldown": (
        "internal_policy",
        ("Per-customer per-channel contact frequency caps to prevent fatigue/harassment. "
        "Redis-backed sliding windows. Internal risk control, not a legal requirement.")
    ),
    "fraud": (
        "internal_policy",
        ("Fraud risk scoring blocks high-risk actions. Thresholds set by merchant risk policy. "
        "Not a specific statutory citation — internal risk management.")
    ),
    "dispute": (
        ("RBI Master Direction — Reserve Bank of India (Regulatory Framework for Recovery Agents), 2023; "
        "Consumer Protection Act, 2019"),
        ("Active payment dispute = immediate halt to all recovery outreach. "
        "Continuing recovery during dispute is prohibited by RBI recovery agent guidelines.")
    ),
    "reversibility": (
        "internal_policy",
        ("Action impact classification (LOW/MEDIUM/HIGH) drives autonomy level. "
        "HIGH-impact actions (financial/irreversible) require human approval per internal RBAC. "
        "Not a statutory rule — internal governance.")
    ),
    "blast_radius": (
        "internal_policy",
        ("Global rate anomaly detection (circuit breaker) to prevent model runaway. "
        "Merchant-level SMS/action ratio monitoring. Internal safety control.")
    ),
    "platform_awareness": (
        "internal_policy",
        ("Suppresses duplicate actions already taken by Razorpay platform "
        "(e.g., platform already sent failure notification, retry pending, active payment link). "
        "Operational deduplication, not a legal requirement.")
    ),

    # Passthrough for internal actions
    "passthrough": (
        "internal_policy",
        "NO_ACTION, WAIT, BLOCK bypass outbound gates — no customer contact, no financial effect."
    ),

    # Statutory instruments carried by the Track E.10 MSMED ladder and E.12 ops console
    "msmed_s16_interest": (
        "Micro, Small and Medium Enterprises Development Act, 2006 §16",
        ("Statutory interest at three times the RBI bank rate with monthly rests, "
        "accruing from day 46 after the invoice (i.e., the day after §15's 45-day "
        "payment timeline). Calculated by the §16 calculator (msmed_interest.py).")
    ),
    "msmed_demand_notice": (
        "Micro, Small and Medium Enterprises Development Act, 2006 §15 & §18",
        ("Formal written demand (invoice reference, accrual dates, principal plus §16 "
        "interest) sent before escalating to conciliation. Issued by the statutory "
        "notice generator at the demand-notice ladder rung.")
    ),
    "msmed_conciliation_filing": (
        "Micro, Small and Medium Enterprises Development Act, 2006 §§18–22",
        ("Filing of the statutory claim with the MSE Facilitation Council / MSME "
        "Samadhaan for conciliation. Never auto-filed: the ladder only produces Filing "
        "Requests that a human approves and dispatches.")
    ),
    "sms_dlt_template": (
        "TRAI Telecom Commercial Communications Customer Preference Regulations (TCCCPR), 2018",
        ("Every SMS must use a DLT-registered, TRAI-approved template; unregistered "
        "templates are non-deliverable and cannot be used for recovery outreach.")
    ),
    "retry_limit": (
        "internal_policy",
        ("Per-channel retry caps that prevent duplicate or harassing contact patterns. "
        "An internal operational control, not a statutory ceiling.")
    ),
    "autopay_execution_window": (
        "internal_policy",
        ("Scheduling guard for mandate/autopay execution attempts (availability hours, "
        "spacing between attempts). Internal execution-window policy; no fixed "
        "statutory clock is claimed.")
    ),
}

# Reverse lookup: which gates are statutory vs internal
STATUTORY_GATES: frozenset[str] = frozenset({
    "consent", "contact_window", "dispute",
    "msmed_s16_interest", "msmed_demand_notice", "msmed_conciliation_filing",
    "sms_dlt_template",
})
INTERNAL_GATES: frozenset[str] = frozenset({
    "customer_preference", "cooldown", "fraud", "reversibility",
    "blast_radius", "platform_awareness", "passthrough",
    "retry_limit", "autopay_execution_window",
})


def get_legal_basis(gate_name: str) -> tuple[str, str]:
    """Return (legal_basis, description) for a gate.

    If gate not found, returns ("unknown", "No legal basis registered").
    """
    return GATE_LEGAL_BASIS.get(gate_name, ("unknown", "No legal basis registered"))


def is_statutory(gate_name: str) -> bool:
    """True if the gate enforces a statutory/regulatory requirement."""
    return gate_name in STATUTORY_GATES


def all_gates() -> dict[str, tuple[str, str]]:
    """Return all registered gates with their legal basis."""
    return dict(GATE_LEGAL_BASIS)