"""Transactional outbox pattern (§8.1).

Persists OutboxCommands transactionally so an external action can never
desync from the DB, in either direction (§8.1, §13.5).
"""

from __future__ import annotations

import dataclasses
import time
import uuid

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
    desync from the DB, in either direction (§8.1, §13.5).
    """

    def __init__(self) -> None:
        # In-memory mock for the hackathon (production: PostgreSQL table)
        self._queue: list[OutboxCommand] = []
        self._processed: set[str] = set()

    def append(self, case_id: str, action: Action, payload: dict) -> OutboxCommand:
        """Append a command to the outbox."""
        command = OutboxCommand(
            command_id=f"cmd_{uuid.uuid4().hex}",
            case_id=case_id,
            action=action,
            payload=payload,
            created_at_ts=time.time(),
        )
        self._queue.append(command)
        return command

    def claim_next(self) -> OutboxCommand | None:
        """Claim the next command from the outbox for processing."""
        for cmd in self._queue:
            if cmd.command_id not in self._processed:
                self._processed.add(cmd.command_id)
                return cmd
        return None

    def mark_processed(self, command_id: str) -> None:
        """Mark a command as successfully processed."""
        self._processed.add(command_id)

    def worker_loop_tick(self) -> int:
        """Simulate a tick of the outbox worker. Returns number of items processed."""
        count = 0
        while True:
            cmd = self.claim_next()
            if not cmd:
                break
            count += 1
            # In a real system, the worker would execute the action here.
            # We just mark it processed for the mock.
            self.mark_processed(cmd.command_id)
        return count
