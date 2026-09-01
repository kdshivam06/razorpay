"""Red-team demo API — 'attack the agent' (§16.1)."""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class RedTeamOutcome:
    """The ATTACK → DETECTED → BLOCKED → REASON result shown on the demo (§16.1)."""

    attack: str
    detected: bool
    blocked: bool
    reason: str


class RedTeamApi:
    """Serves the interactive 'Attack the Agent' demo endpoints (§16.1).

    Exercises: invalid/duplicate/out-of-order webhooks, fake captured, prompt
    injection, wrong person, opt-out, dispute halt, API timeout, DB/Redis/
    Razorpay outage, duplicate run, expired link, partial payment, late auth.
    """

    def run_attack(self, attack_name: str, payload: dict) -> RedTeamOutcome:
        raise NotImplementedError("TODO: Policy track — see implementation_plan.md §16.1")