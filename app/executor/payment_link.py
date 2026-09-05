"""Payment link lifecycle protection (§8.4).

Principles:
  existing active link → reuse it
  expired  → create a replacement
  paid     → close the recovery
  partially paid → recover only the remaining amount
"""

from __future__ import annotations

import dataclasses
import logging
import time
import uuid

import razorpay
from requests.exceptions import ConnectionError as ReqConnectionError
from requests.exceptions import ReadTimeout

from app.config import get_settings
from app.contracts import PaymentLinkState

logger = logging.getLogger(__name__)

# Razorpay API timeout in seconds (matches action_executor)
_RAZORPAY_TIMEOUT_S = 10

# Hosted payment page (final redirect target of short_url in Test Mode)
_PAYMENT_LINK_PAGE = "https://razorpay.com/payment-link/{link_id}"


class PaymentLinkGatewayError(Exception):
    """A Razorpay Payment Links API call failed (§3.5 UNKNOWN semantics).

    kind is machine-readable: TIMEOUT / API_ERROR / CONFIG. A timeout is
    never treated as success/failure — it routes to reconciliation.
    """

    def __init__(self, kind: str, reason: str) -> None:
        super().__init__(reason)
        self.kind = kind
        self.reason = reason


class RazorpayPaymentLinkClient:
    """Real Razorpay Test Mode Payment Links gateway (E.9).

    Wraps razorpay-python using keys from app.config. When keys are absent
    (or construction fails) the client is UNAVAILABLE and callers fall back
    to a mock link — mirroring the action_executor razorpay pattern.
    """

    def __init__(
        self,
        *,
        key_id: str | None = None,
        key_secret: str | None = None,
        timeout_s: int = _RAZORPAY_TIMEOUT_S,
    ) -> None:
        self._timeout_s = timeout_s
        self._client = None
        try:
            settings = get_settings()
            kid = key_id or settings.RAZORPAY_KEY_ID
            ks = key_secret or settings.RAZORPAY_KEY_SECRET
            if kid and ks:
                self._client = razorpay.Client(auth=(kid, ks))
                self._client.set_app_details(
                    {"title": "RecoveryOS", "version": "0.1.0"}
                )
                logger.info("Razorpay Payment Links client initialized (Test Mode)")
        except Exception:  # noqa: BLE001 - unavailable is a graceful fallback
            self._client = None
            logger.warning(
                "Razorpay Payment Links client not initialized — mock mode only"
            )

    @property
    def available(self) -> bool:
        return self._client is not None

    def create_link(
        self,
        *,
        case_id: str,
        amount_paise: int,
        description: str,
        notes: dict[str, str] | None = None,
        customer: dict[str, str] | None = None,
        reference_id: str | None = None,
        expire_by: int | None = None,
    ) -> dict:
        """Create a Payment Link via the Razorpay Test Mode API.

        Returns the API entity dict (id, short_url, status, amount, ...).
        Raises PaymentLinkGatewayError on any failure — timeouts are
        UNKNOWN-style and must never be blindly retried (§3.5).
        """
        if self._client is None:
            raise PaymentLinkGatewayError(
                "CONFIG", "Razorpay client not configured (RAZORPAY_KEY_ID/SECRET)"
            )
        data: dict = {
            "amount": amount_paise,
            "currency": "INR",
            "description": description,
            "notify": {"sms": True, "email": True},
            "reminder_enable": True,
        }
        if notes:
            data["notes"] = notes
        if customer:
            data["customer"] = customer
        if reference_id:
            data["reference_id"] = reference_id[:40]
        if expire_by:
            data["expire_by"] = int(expire_by)
        try:
            return self._client.payment_link.create(data)
        except (ReadTimeout, ReqConnectionError, TimeoutError) as exc:
            raise PaymentLinkGatewayError(
                "TIMEOUT", f"{type(exc).__name__}: {exc}"
            ) from exc
        except Exception as exc:
            raise PaymentLinkGatewayError("API_ERROR", str(exc)) from exc


@dataclasses.dataclass(frozen=True)
class PaymentLinkRecord:
    """Full lifecycle state, NOT just active=true/false (§8.4)."""

    link_id: str
    state: PaymentLinkState
    amount_paid_paise: int
    amount_outstanding_paise: int
    expires_at_ts: float | None
    razorpay_link_id: str | None = None
    short_url: str | None = None
    payment_url: str | None = None


class PaymentLinkLifecycle:
    """Manages CREATED → PARTIALLY_PAID → PAID → CANCELLED → EXPIRED (§8.4)."""

    def __init__(self) -> None:
        # mock db: case_id -> list[PaymentLinkRecord]
        self._links: dict[str, list[PaymentLinkRecord]] = {}

    def resolve_outstanding(self, case_id: str, invoice_amount_paise: int) -> int:
        """Calculate what remains to be collected given all active/partially paid links."""
        links = self._links.get(case_id, [])
        paid = sum(link.amount_paid_paise for link in links)
        return max(0, invoice_amount_paise - paid)

    def create_or_reuse(
        self,
        case_id: str,
        amount_paise: int = 0,
        *,
        gateway: RazorpayPaymentLinkClient | None = None,
    ) -> PaymentLinkRecord:
        """Reuse an active link if it exists and covers the amount, else create.

        When a Razorpay gateway is supplied, a NEW link is created through the
        real Test Mode Payment Links API and the returned id/URLs are stored on
        the record. Reused active links keep whatever id/URLs they were created
        with. Gateway failures propagate as PaymentLinkGatewayError (§3.5).
        """
        links = self._links.setdefault(case_id, [])

        # 1. Check for existing active link we can reuse
        for link in links:
            if link.state in {
                PaymentLinkState.CREATED,
                PaymentLinkState.PARTIALLY_PAID,
            }:
                if link.expires_at_ts and link.expires_at_ts < time.time():
                    # Link expired in reality, update state
                    self._update_state(case_id, link.link_id, PaymentLinkState.EXPIRED)
                    continue

                if link.amount_outstanding_paise >= amount_paise:
                    logger.info(
                        "Reusing existing active payment link %s for case %s",
                        link.link_id,
                        case_id,
                    )
                    return link

        # 2. Need a new link
        logger.info(
            "Creating new payment link for case %s (amount: ₹%s)",
            case_id,
            amount_paise / 100,
        )
        derived_id = f"plink_{uuid.uuid4().hex[:14]}"
        razorpay_link_id: str | None = None
        short_url = f"https://rzp.io/i/{derived_id}"
        payment_url = _PAYMENT_LINK_PAGE.format(link_id=derived_id)

        if gateway is not None:
            if not gateway.available:
                logger.warning(
                    "Razorpay gateway unavailable for case %s — creating mock link",
                    case_id,
                )
            else:
                resp = gateway.create_link(
                    case_id=case_id,
                    amount_paise=amount_paise,
                    description=f"Recovery for case {case_id}",
                    notes={"case_id": case_id},
                    reference_id=f"RC-{case_id}",
                    expire_by=int(time.time()) + 86400 * 3,  # 3 days default expiry
                )
                razorpay_link_id = resp.get("id")
                short_url = resp.get("short_url") or short_url
                payment_url = (
                    _PAYMENT_LINK_PAGE.format(link_id=razorpay_link_id)
                    if razorpay_link_id
                    else payment_url
                )

        link_id = razorpay_link_id or derived_id
        new_link = PaymentLinkRecord(
            link_id=link_id,
            state=PaymentLinkState.CREATED,
            amount_paid_paise=0,
            amount_outstanding_paise=amount_paise,
            expires_at_ts=time.time() + 86400 * 3,  # 3 days default expiry
            razorpay_link_id=razorpay_link_id,
            short_url=short_url,
            payment_url=payment_url,
        )
        links.append(new_link)
        return new_link

    def cancel_active_link(self, case_id: str) -> None:
        """Cancel any currently active payment links for this case."""
        links = self._links.get(case_id, [])
        for link in links:
            if link.state in {
                PaymentLinkState.CREATED,
                PaymentLinkState.PARTIALLY_PAID,
            }:
                logger.info(
                    "Cancelling active payment link %s for case %s",
                    link.link_id,
                    case_id,
                )
                self._update_state(case_id, link.link_id, PaymentLinkState.CANCELLED)

    def get(self, link_id: str) -> PaymentLinkRecord:
        """Get a payment link by ID."""
        for links in self._links.values():
            for link in links:
                if link.link_id == link_id:
                    return link
        raise ValueError(f"Payment link {link_id} not found")

    def latest(self, case_id: str) -> PaymentLinkRecord | None:
        """Return the most recently created link for a case, if any."""
        links = self._links.get(case_id, [])
        return links[-1] if links else None

    def mark_paid(
        self,
        case_id: str,
        link_id: str,
        *,
        amount_paid_paise: int,
    ) -> PaymentLinkRecord:
        """Close a link as PAID: full amount collected, outstanding zero."""
        links = self._links.get(case_id, [])
        for i, link in enumerate(links):
            if link.link_id == link_id:
                paid = links[i] = dataclasses.replace(
                    link,
                    state=PaymentLinkState.PAID,
                    amount_paid_paise=amount_paid_paise,
                    amount_outstanding_paise=max(
                        0, link.amount_outstanding_paise - amount_paid_paise
                    ),
                )
                logger.info(
                    "Payment link %s for case %s marked PAID (₹%s)",
                    link_id,
                    case_id,
                    amount_paid_paise / 100,
                )
                return paid
        raise ValueError(f"Payment link {link_id} not found for case {case_id}")

    def _update_state(
        self, case_id: str, link_id: str, new_state: PaymentLinkState
    ) -> None:
        links = self._links.get(case_id, [])
        for i, link in enumerate(links):
            if link.link_id == link_id:
                links[i] = dataclasses.replace(link, state=new_state)
                break
