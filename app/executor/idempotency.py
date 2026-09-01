"""Idempotency key management (§16.1, §8.3)."""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class IdempotencyRecord:
    """One idempotency key usage serving a single logical request."""

    key: str
    scope: str
    hash: str
    state: str


class IdempotencyManager:
    """Ensures duplicate pipeline runs or API calls never produce duplicate
    external effects (§16.1, §13.5)."""

    def new_key(self, scope: str) -> str:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §16.1")

    def check(self, key: str) -> bool:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §16.1")

    def release(self, key: str) -> None:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §16.1")