"""Control group — 90/10 treatment/control split (§12.1)."""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class Assignment:
    """Control-group assignment for a case."""

    case_id: str
    group: str  # "treatment" | "control"


class ControlGroup:
    """Splits the synthetic batch 90% treatment / 10% control (§12.1).

    Control receives NO automated intervention and proves incremental lift:
      Control payment rate vs Treatment payment rate — NOT '54% recovered'."""

    def assign(self, case_id: str) -> Assignment:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §12.1")

    def split(
        self, case_ids: list[str], treatment_ratio: float = 0.9
    ) -> tuple[list[str], list[str]]:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §12.1")