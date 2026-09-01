"""Decision trace — full structured decision record (§13.3, §13.4)."""

from __future__ import annotations

import dataclasses

from app.contracts import Action


@dataclasses.dataclass(frozen=True)
class DecisionTrace:
    """The complete structured trail for one case (§13.3, §13.4).

    Judges click 'WHY DID YOU SEND THIS?' and see every step."""

    case_id: str
    event: str
    state: str
    root_cause: str
    risk: dict
    natural_payment_probability: float
    candidate_actions: list[Action]
    per_action_uplift: dict[Action, float]
    per_action_score: dict[Action, float]
    policy_gate: dict
    selected_action: Action | None
    rejected_reasons: dict[Action, str]
    execution_result: str | None
    outcome: str | None


class DecisionTracer:
    """Builds and stores the DecisionTrace for a case (§13.3)."""

    def build(self, **kwargs: object) -> DecisionTrace:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §13.3")

    def why_not(self, case_id: str, chosen: Action) -> dict[Action, str]:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §13.4")