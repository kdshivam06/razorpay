"""Customer preference engine — legal ∩ merchant ∩ preference ∩ availability (§7.4).

Computes the intersection of four constraint layers:
  1. Legal window (regulatory per channel — from ContactPolicyEngine)
  2. Merchant policy (merchant-configured overrides)
  3. Customer preference (customer-stated DNC hours/channels)
  4. Channel availability (is the channel up?)

Result: the narrowest allowed window for contacting this customer.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, time, timedelta

import pytz

from app.policy.contact_policy import Channel, ContactPolicyEngine

IST = pytz.timezone("Asia/Kolkata")


@dataclasses.dataclass(frozen=True)
class ContactPreference:
    """One customer's preference over channels and windows."""

    customer_id: str
    preferred_channels: tuple[str, ...] = ()
    blocked_channels: tuple[str, ...] = ()
    do_not_call_before: str = "08:00"
    do_not_call_after: str = "19:00"
    allow_sunday: bool = True


@dataclasses.dataclass(frozen=True)
class MerchantPolicy:
    """Merchant-level contact constraints."""

    merchant_id: str
    allow_sunday: bool = True
    allow_saturday: bool = True
    contact_start: str = "08:00"
    contact_end: str = "21:00"
    blocked_channels: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class EffectiveWindow:
    """The final computed intersection window."""

    start: time
    end: time
    allowed: bool
    reason: str = ""


class CustomerPreferenceEngine:
    """Intersects the four contact constraints:

    legal_window ∩ merchant_policy ∩ customer_preference ∩ channel_availability

    Example from §7.4:
        Allowed: 08:00–19:00
        Customer preference: 10:00–12:00
        Merchant: No Sunday calls
        → Final: 10:00–12:00 Mon–Sat
    """

    def __init__(
        self,
        contact_engine: ContactPolicyEngine | None = None,
    ) -> None:
        self._contact = contact_engine or ContactPolicyEngine()
        # In-memory stores — production would use PostgreSQL
        self._preferences: dict[str, ContactPreference] = {}
        self._merchant_policies: dict[str, MerchantPolicy] = {}
        # Channel availability (True = up)
        self._channel_availability: dict[str, bool] = {ch.value: True for ch in Channel}

    def set_preference(self, pref: ContactPreference) -> None:
        """Store a customer's contact preferences."""
        self._preferences[pref.customer_id] = pref

    def set_merchant_policy(self, policy: MerchantPolicy) -> None:
        """Store a merchant's contact policy."""
        self._merchant_policies[policy.merchant_id] = policy

    def set_channel_availability(self, channel: str, available: bool) -> None:
        """Mark a channel as up or down."""
        self._channel_availability[channel] = available

    def load_preferences(self, customer_id: str) -> ContactPreference:
        """Load a customer's preferences, returning defaults if none stored."""
        return self._preferences.get(
            customer_id,
            ContactPreference(customer_id=customer_id),
        )

    def can_contact(
        self,
        customer_id: str,
        channel: str,
        at: datetime,
        merchant_id: str = "default",
    ) -> bool:
        """The full intersection check: legal ∩ merchant ∩ customer ∩ availability."""
        window = self.effective_window(customer_id, channel, at, merchant_id)
        return window.allowed

    def effective_window(
        self,
        customer_id: str,
        channel: str,
        at: datetime,
        merchant_id: str = "default",
    ) -> EffectiveWindow:
        """Compute the effective contact window at a given time.

        Returns an EffectiveWindow with allowed=False and reason if blocked.
        """
        # 1. Channel availability
        if not self._channel_availability.get(channel, False):
            return EffectiveWindow(
                start=time(0, 0),
                end=time(0, 0),
                allowed=False,
                reason=f"channel {channel} is unavailable",
            )

        # 2. Legal window (contact policy engine)
        try:
            ch_enum = Channel(channel)
        except ValueError:
            return EffectiveWindow(
                start=time(0, 0),
                end=time(0, 0),
                allowed=False,
                reason=f"unknown channel: {channel}",
            )

        if not self._contact.is_contact_allowed(ch_enum, at):
            return EffectiveWindow(
                start=time(0, 0),
                end=time(0, 0),
                allowed=False,
                reason=f"outside legal contact window for {channel}",
            )

        # 3. Merchant policy
        merchant = self._merchant_policies.get(merchant_id)
        if merchant:
            try:
                local = at.astimezone(IST)
            except (ValueError, AttributeError):
                local = IST.localize(at) if at.tzinfo is None else at

            if channel in merchant.blocked_channels:
                return EffectiveWindow(
                    start=time(0, 0),
                    end=time(0, 0),
                    allowed=False,
                    reason=f"merchant {merchant_id} blocks channel {channel}",
                )

            if local.weekday() == 6 and not merchant.allow_sunday:
                return EffectiveWindow(
                    start=time(0, 0),
                    end=time(0, 0),
                    allowed=False,
                    reason=f"merchant {merchant_id} disallows Sunday contact",
                )

            if local.weekday() == 5 and not merchant.allow_saturday:
                return EffectiveWindow(
                    start=time(0, 0),
                    end=time(0, 0),
                    allowed=False,
                    reason=f"merchant {merchant_id} disallows Saturday contact",
                )

            m_start = _parse_time(merchant.contact_start)
            m_end = _parse_time(merchant.contact_end)
            if not (m_start <= local.time() <= m_end):
                return EffectiveWindow(
                    start=m_start,
                    end=m_end,
                    allowed=False,
                    reason=f"outside merchant window {merchant.contact_start}–{merchant.contact_end}",
                )

        # 4. Customer preference
        pref = self.load_preferences(customer_id)

        if channel in pref.blocked_channels:
            return EffectiveWindow(
                start=time(0, 0),
                end=time(0, 0),
                allowed=False,
                reason=f"customer {customer_id} blocked channel {channel}",
            )

        try:
            local = at.astimezone(IST)
        except (ValueError, AttributeError):
            local = IST.localize(at) if at.tzinfo is None else at

        if local.weekday() == 6 and not pref.allow_sunday:
            return EffectiveWindow(
                start=time(0, 0),
                end=time(0, 0),
                allowed=False,
                reason=f"customer {customer_id} disallows Sunday contact",
            )

        cust_start = _parse_time(pref.do_not_call_before)
        cust_end = _parse_time(pref.do_not_call_after)
        if not (cust_start <= local.time() <= cust_end):
            return EffectiveWindow(
                start=cust_start,
                end=cust_end,
                allowed=False,
                reason=f"outside customer preference {pref.do_not_call_before}–{pref.do_not_call_after}",
            )

        # All checks passed — window is the intersection
        return EffectiveWindow(
            start=cust_start,
            end=cust_end,
            allowed=True,
        )

    def allowed_window(
        self, customer_id: str, channel: str, at: datetime
    ) -> tuple[datetime, datetime]:
        """Return the next allowed window as a (start, end) datetime tuple."""
        pref = self.load_preferences(customer_id)
        start = _parse_time(pref.do_not_call_before)
        end = _parse_time(pref.do_not_call_after)

        try:
            local = at.astimezone(IST)
        except (ValueError, AttributeError):
            local = IST.localize(at) if at.tzinfo is None else at

        window_start = local.replace(
            hour=start.hour, minute=start.minute, second=0, microsecond=0
        )
        window_end = local.replace(
            hour=end.hour, minute=end.minute, second=0, microsecond=0
        )

        # If we're past today's window, return tomorrow's
        if local.time() > end:
            window_start += timedelta(days=1)
            window_end += timedelta(days=1)

        return (window_start, window_end)


def _parse_time(t: str) -> time:
    """Parse 'HH:MM' to a time object."""
    parts = t.split(":")
    return time(int(parts[0]), int(parts[1]))
