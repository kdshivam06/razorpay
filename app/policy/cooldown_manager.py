"""Redis-based cooldown and rate limiting per customer per channel (§7.1).

FAILS CLOSED: if Redis is unavailable, outbound actions are refused (§2.4).
This is the most critical safety property of the cooldown layer.

Cooldowns use exponential backoff: each successive contact on the same
channel doubles the cooldown window, up to a configurable ceiling.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

try:
    import redis as _redis
except ImportError:  # pragma: no cover
    _redis = None  # type: ignore[assignment]

_RedisClient = Any if _redis is None else _redis.Redis


# Default cooldown durations in seconds, per channel
_DEFAULT_COOLDOWNS: dict[str, int] = {
    "SMS": 4 * 3600,  # 4 hours
    "WHATSAPP": 4 * 3600,  # 4 hours
    "EMAIL": 12 * 3600,  # 12 hours
    "VOICE_CALL": 24 * 3600,  # 24 hours
    "PAYMENT_LINK": 0,  # no cooldown (link creation is idempotent)
    "sms": 4 * 3600,
    "whatsapp": 4 * 3600,
    "email": 12 * 3600,
    "voice": 24 * 3600,
}

# Maximum backoff ceiling in seconds (48 hours)
_MAX_BACKOFF_SECONDS = 48 * 3600


class CooldownManager:
    """Persists per-customer per-channel cooldowns in Redis. Fails CLOSED:
    if Redis is unavailable, outbound actions are refused (§2.4).

    Key format: recovery:cooldown:{customer_id}:{channel}
    Backoff key: recovery:backoff:{customer_id}:{channel}
    """

    def __init__(self, client: _RedisClient | None = None) -> None:
        self._client = client
        self._cooldowns = dict(_DEFAULT_COOLDOWNS)
        self._available: bool | None = None  # cached health check

    @property
    def redis_available(self) -> bool:
        """Check if Redis is reachable. Result is cached after first call."""
        if self._client is None:
            return False
        if self._available is not None:
            return self._available
        try:
            self._client.ping()
            self._available = True
        except Exception:  # noqa: BLE001
            logger.warning("Redis unavailable — cooldown manager will FAIL CLOSED")
            self._available = False
        return self._available

    def _reset_availability_cache(self) -> None:
        """Force re-check on next call (used after reconnection)."""
        self._available = None

    def within_cooldown(self, customer_id: str, channel: str) -> bool:
        """Return True if the customer is still in cooldown on this channel.

        FAILS CLOSED: if Redis is unreachable, returns True (= in cooldown
        = action blocked).  This is the §2.4 guarantee.
        """
        if not self.redis_available:
            logger.warning(
                "Redis down — FAIL CLOSED: treating %s/%s as within cooldown",
                customer_id,
                channel,
            )
            return True  # FAIL CLOSED

        key = f"recovery:cooldown:{customer_id}:{channel.upper()}"
        try:
            return bool(self._client.exists(key))
        except Exception:
            logger.exception("Redis error checking cooldown — FAIL CLOSED")
            self._available = False
            return True  # FAIL CLOSED

    def touch(
        self,
        customer_id: str,
        channel: str,
        ttl_seconds: int | None = None,
    ) -> None:
        """Set the cooldown for a customer/channel. Uses default TTL if not provided.

        Also increments the backoff counter for exponential backoff.
        """
        if not self.redis_available:
            logger.warning(
                "Redis down — cannot set cooldown for %s/%s", customer_id, channel
            )
            return

        ch = channel.upper()
        ttl = (
            ttl_seconds
            if ttl_seconds is not None
            else self._cooldowns.get(ch, 4 * 3600)
        )
        key = f"recovery:cooldown:{customer_id}:{ch}"
        backoff_key = f"recovery:backoff:{customer_id}:{ch}"

        try:
            # Get current backoff level
            level = self._client.get(backoff_key)
            backoff_level = int(level) if level else 0

            # Apply exponential backoff: ttl * 2^level
            effective_ttl = min(ttl * (2**backoff_level), _MAX_BACKOFF_SECONDS)

            self._client.setex(key, int(effective_ttl), "1")
            # Increment backoff level (expires after 7 days)
            self._client.setex(backoff_key, 7 * 24 * 3600, str(backoff_level + 1))
        except Exception:
            logger.exception("Redis error setting cooldown — ignoring (fail-safe)")
            self._available = False

    def backoff_seconds(self, customer_id: str, channel: str) -> int:
        """Return the remaining cooldown TTL in seconds.

        FAILS CLOSED: if Redis is unreachable, returns a large value
        (= action blocked until Redis recovers).
        """
        if not self.redis_available:
            return _MAX_BACKOFF_SECONDS  # FAIL CLOSED

        key = f"recovery:cooldown:{customer_id}:{channel.upper()}"
        try:
            ttl = self._client.ttl(key)
            return max(0, int(ttl)) if ttl and ttl > 0 else 0
        except Exception:
            logger.exception("Redis error checking backoff — FAIL CLOSED")
            self._available = False
            return _MAX_BACKOFF_SECONDS  # FAIL CLOSED

    def clear(self, customer_id: str, channel: str) -> None:
        """Clear cooldown for a customer/channel (e.g. after successful payment)."""
        if not self.redis_available:
            return
        key = f"recovery:cooldown:{customer_id}:{channel.upper()}"
        backoff_key = f"recovery:backoff:{customer_id}:{channel.upper()}"
        try:
            self._client.delete(key, backoff_key)
        except Exception:
            logger.exception("Redis error clearing cooldown")
            self._available = False
