"""Contact policy engine — configurable contact windows (§7.2).

NOT hardcoded "RBI = 8 AM–7 PM".  Each channel has its own window,
configurable per merchant.  A 7:01 PM action MUST be held until 8:00 AM
the next allowed day.
"""

from __future__ import annotations

import dataclasses
import enum
from datetime import datetime, time, timedelta

import pytz

IST = pytz.timezone("Asia/Kolkata")


class Channel(str, enum.Enum):
    VOICE = "voice"
    SMS = "sms"
    EMAIL = "email"
    WHATSAPP = "whatsapp"


@dataclasses.dataclass(frozen=True)
class ChannelWindow:
    """A configurable per-channel allowed window (NOT hardcoded RBI hours)."""

    channel: Channel
    start: time
    end: time
    timezone_name: str = "Asia/Kolkata"
    allow_sunday: bool = True


# Default windows per §7.2 YAML example
_DEFAULT_WINDOWS: dict[Channel, ChannelWindow] = {
    Channel.VOICE: ChannelWindow(
        channel=Channel.VOICE,
        start=time(8, 0),
        end=time(19, 0),
        allow_sunday=False,
    ),
    Channel.SMS: ChannelWindow(
        channel=Channel.SMS,
        start=time(8, 0),
        end=time(21, 0),
    ),
    Channel.EMAIL: ChannelWindow(
        channel=Channel.EMAIL,
        start=time(0, 0),
        end=time(23, 59),
    ),
    Channel.WHATSAPP: ChannelWindow(
        channel=Channel.WHATSAPP,
        start=time(8, 0),
        end=time(21, 0),
    ),
}


class ContactWindowEvaluator:
    """Evaluates contact legality at a given timestamp."""

    def __init__(self, windows: dict[Channel, ChannelWindow] | None = None) -> None:
        self._windows = windows or dict(_DEFAULT_WINDOWS)

    def allowed(self, channel: Channel, at: datetime) -> bool:
        """Return True if contacting via *channel* at *at* is within the window."""
        window = self._windows.get(channel)
        if window is None:
            return False  # FAIL CLOSED: unknown channel → block

        tz = pytz.timezone(window.timezone_name)
        try:
            local = at.astimezone(tz)
        except (ValueError, AttributeError):
            local = tz.localize(at) if at.tzinfo is None else at

        # Sunday check
        if local.weekday() == 6 and not window.allow_sunday:
            return False

        current_time = local.time()
        return window.start <= current_time <= window.end

    def next_allowed_time(self, channel: Channel, at: datetime) -> datetime:
        """Compute the next allowed send time if *at* falls outside the window.

        A 7:01 PM action → 8:00 AM next allowed day.
        """
        window = self._windows.get(channel)
        if window is None:
            raise ValueError(f"No window configured for channel {channel.value}")

        tz = pytz.timezone(window.timezone_name)
        try:
            local = at.astimezone(tz)
        except (ValueError, AttributeError):
            local = tz.localize(at) if at.tzinfo is None else at

        # If we're within the window, return the original time
        if self.allowed(channel, at):
            return at

        current_time = local.time()

        # If before the window start today, snap to today's start
        if current_time < window.start:
            candidate = local.replace(
                hour=window.start.hour,
                minute=window.start.minute,
                second=0,
                microsecond=0,
            )
        else:
            # Past the window end — snap to next day's start
            candidate = (local + timedelta(days=1)).replace(
                hour=window.start.hour,
                minute=window.start.minute,
                second=0,
                microsecond=0,
            )

        # Skip disallowed days (e.g. Sunday for voice)
        for _ in range(8):  # guard against infinite loop
            if candidate.weekday() == 6 and not window.allow_sunday:
                candidate += timedelta(days=1)
            else:
                break

        return candidate


class ContactPolicyEngine:
    """Enforces merchant-defined, configurable contact windows per channel,
    never a blanket 'RBI = 8 AM–7 PM' rule (§7.2)."""

    def __init__(self, windows: dict[Channel, ChannelWindow] | None = None) -> None:
        self._evaluator = ContactWindowEvaluator(windows)
        self._windows = windows or dict(_DEFAULT_WINDOWS)

    def load_from_yaml(self, path: str) -> None:
        """Load channel windows from a YAML config file.

        Expected format per §7.2:
            contact_policy:
              voice:
                start: "08:00"
                end: "19:00"
              sms:
                start: "08:00"
                end: "21:00"
        """
        import yaml

        with open(path, encoding="utf-8") as f:
            config = yaml.safe_load(f)

        policy = config.get("contact_policy", {})
        tz_name = policy.get("timezone", "Asia/Kolkata")

        for channel in Channel:
            if channel.value in policy:
                ch_cfg = policy[channel.value]
                start_parts = ch_cfg["start"].split(":")
                end_parts = ch_cfg["end"].split(":")
                self._windows[channel] = ChannelWindow(
                    channel=channel,
                    start=time(int(start_parts[0]), int(start_parts[1])),
                    end=time(int(end_parts[0]), int(end_parts[1])),
                    timezone_name=tz_name,
                    allow_sunday=ch_cfg.get("allow_sunday", True),
                )
        self._evaluator = ContactWindowEvaluator(self._windows)

    def is_contact_allowed(
        self,
        channel: Channel,
        at: datetime,
        customer_preference: tuple[str, ...] = (),
    ) -> bool:
        """Check if contacting via channel at the given time is permitted.

        Considers the channel window AND optional customer preferences.
        """
        if not self._evaluator.allowed(channel, at):
            return False

        # If customer has blocked this channel, deny
        return channel.value not in customer_preference

    def next_allowed(self, channel: Channel, at: datetime) -> datetime:
        """When can we next contact via this channel?"""
        return self._evaluator.next_allowed_time(channel, at)
