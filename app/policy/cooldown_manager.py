"""Redis-based cooldown and rate limiting per customer per channel (§7.1)."""

from __future__ import annotations

from typing import Any

try:  # redis is an optional runtime dep for the contract stub
    import redis as _redis
except ImportError:  # pragma: no cover - only when redis is absent
    _redis = None  # type: ignore[assignment]

_RedisClient = Any if _redis is None else _redis.Redis


class CooldownManager:
    """Persists per-customer per-channel cooldowns in Redis. Fails CLOSED:
    if Redis is unavailable, outbound actions are refused (§16.1)."""

    def __init__(self, client: _RedisClient | None = None) -> None:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §7.1, §16.1")

    def within_cooldown(self, customer_id: str, channel: str) -> bool:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §7.1")

    def touch(self, customer_id: str, channel: str, ttl_seconds: int) -> None:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §7.1")

    def backoff_seconds(self, customer_id: str, channel: str) -> int:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §7.1")