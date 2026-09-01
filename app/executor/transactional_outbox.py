"""Transactional outbox pattern (§8.1)."""

from __future__ import annotations

import dataclasses

from app.contracts import Action


@dataclasses.dataclass(frozen=True)
class OutboxCommand:
    """A decision + outbox command committed in ONE DB transaction (§8.1)."""

    command_id: str
    case_id: str
    action: Action
    payload: dict
    created_at_ts: float


class TransactionalOutbox:
    """Persists OutboxCommands transactionally so an external action can never
    desync from the DB, in either direction (§8.1, §13.5)."""

    def append(self, command: OutboxCommand) -> None:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §8.1")

    def claim_next(self) -> OutboxCommand | None:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §8.1")

    def worker_loop_tick(self) -> int:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §8.1")