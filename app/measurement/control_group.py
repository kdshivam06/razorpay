"""Control group — 90/10 treatment/control split (§12.1)."""

from __future__ import annotations

import dataclasses
import hashlib


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
        bucket = _stable_bucket(case_id)
        group = "control" if bucket >= 90 else "treatment"
        return Assignment(case_id=case_id, group=group)

    def split(
        self, case_ids: list[str], treatment_ratio: float = 0.9
    ) -> tuple[list[str], list[str]]:
        if not 0.0 <= treatment_ratio <= 1.0:
            raise ValueError("treatment_ratio must be between 0 and 1")
        ordered = sorted(case_ids, key=_stable_bucket_for_sort)
        treatment_count = round(len(ordered) * treatment_ratio)
        treatment = sorted(ordered[:treatment_count])
        control = sorted(ordered[treatment_count:])
        return treatment, control


def _stable_bucket(case_id: str) -> int:
    digest = hashlib.sha256(case_id.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 100


def _stable_bucket_for_sort(case_id: str) -> tuple[int, str]:
    digest = hashlib.sha256(case_id.encode("utf-8")).hexdigest()
    return int(digest, 16), case_id
