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
        "TRAI DLT registration & explicit consent required for commercial SMS/voice. "
        "Consent must be specific to purpose (payment_recovery), scoped, timestamped, and revocable."
    ),
    "contact_window": (
        "RBI Master Direction — Reserve Bank of India (Regulatory Framework for Recovery Agents), 2023; "
        "TRAI TCCCPR 2018",
        "Contact hours for recovery communications. Voice: 08:00–19:00 (RBI recovery agent rule). "
        "SMS/Email/WhatsApp: configurable per merchant/customer preference (TRAI permits wider windows). "
        "Do NOT hard-code RBI 8–7 PM for all channels."
    ),

    # Platform / contract gates (not statutory)
    "customer_preference": (
        "internal_policy",
        "Customer-stated DND/channel/time preferences. Enforced as: "
        "legal_window ∩ merchant_policy ∩ customer_preference ∩ channel_availability. "
        "Not a statutory rule — merchant/customer contract terms."
    ),
    "cooldown": (
        "internal_policy",
        "Per-customer per-channel contact frequency caps to prevent fatigue/harassment. "
        "Redis-backed sliding windows. Internal risk control, not a legal requirement."
    ),
    "fraud": (
        "internal_policy",
        "Fraud risk scoring blocks high-risk actions. Thresholds set by merchant risk policy. "
        "Not a specific statutory citation — internal risk management."
    ),
    "dispute": (
        "RBI Master Direction — Reserve Bank of India (Regulatory Framework for Recovery Agents), 2023; "
        "Consumer Protection Act, 2019",
        "Active payment dispute = immediate halt to all recovery outreach. "
        "Continuing recovery during dispute is prohibited by RBI recovery agent guidelines."
    ),
    "reversibility": (
        "internal_policy",
        "Action impact classification (LOW/MEDIUM/HIGH) drives autonomy level. "
        "HIGH-impact actions (financial/irreversible) require human approval per internal RBAC. "
        "Not a statutory rule — internal governance."
    ),
    "blast_radius": (
        "internal_policy",
        "Global rate anomaly detection (circuit breaker) to prevent model runaway. "
        "Merchant-level SMS/action ratio monitoring. Internal safety control."
    ),
    "platform_awareness": (
        "internal_policy",
        "Suppresses duplicate actions already taken by Razorpay platform "
        "(e.g., platform already sent failure notification, retry pending, active payment link). "
        "Operational deduplication, not a legal requirement."
    ),

    # Passthrough for internal actions
    "passthrough": (
        "internal_policy",
        "NO_ACTION, WAIT, BLOCK bypass outbound gates — no customer contact, no financial effect."
    ),
}

# Reverse lookup: which gates are statutory vs internal
STATUTORY_GATES: frozenset[str] = frozenset({"consent", "contact_window", "dispute"})
INTERNAL_GATES: frozenset[str] = frozenset({
    "customer_preference", "cooldown", "fraud", "reversibility",
    "blast_radius", "platform_awareness", "passthrough"
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