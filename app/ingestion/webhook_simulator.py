"""Dev-only webhook simulation — build + sign a Razorpay event (E.9).

The signature scheme is identical to Razorpay production webhooks
(HMAC-SHA256 over the raw request body), so a simulated event flows through
the SAME gateway every real webhook does: validate_event → freshness →
dedup → inbox (or DLQ). Nothing here reaches the network.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid


def build_payment_link_paid_event(
    *,
    link_id: str,
    case_id: str,
    amount_paise: int,
    short_url: str | None = None,
    currency: str = "INR",
    event_id: str | None = None,
    payment_id: str | None = None,
    created_at: int | None = None,
) -> dict:
    """Build a Razorpay-shaped `payment_link.paid` event payload.

    Mirrors the real webhook schema: fresh `created_at` (epoch seconds),
    unique event `id`, and payment_link + payment entities nested under
    `payload`. `notes.case_id` lets downstream handlers tie the payment
    back to the recovery case.
    """
    created = int(created_at if created_at is not None else time.time())
    plink_id = str(link_id)
    pay_id = str(payment_id or f"pay_sim_{uuid.uuid4().hex[:14]}")
    return {
        "entity": "event",
        "event": "payment_link.paid",
        "account_id": "acc_sim_e9",
        "id": str(event_id or f"evt_sim_plpaid_{uuid.uuid4().hex[:14]}"),
        "created_at": created,
        "payload": {
            "payment_link": {
                "entity": {
                    "id": plink_id,
                    "amount": int(amount_paise),
                    "currency": currency,
                    "status": "paid",
                    "amount_paid": int(amount_paise),
                    "short_url": short_url or "",
                    "notes": {"case_id": case_id},
                }
            },
            "payment": {
                "entity": {
                    "id": pay_id,
                    "amount": int(amount_paise),
                    "currency": currency,
                    "status": "captured",
                    "method": "upi",
                }
            },
        },
    }


def encode_event(event: dict, secret: str) -> tuple[bytes, str]:
    """Serialize an event and sign it exactly like Razorpay.

    Returns (body_bytes, "sha256=<hex>") — the `X-Razorpay-Signature`
    header value the validator expects (webhook_validator.parse_signature).
    """
    body = json.dumps(event, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return body, f"sha256={signature}"