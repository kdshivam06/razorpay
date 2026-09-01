"""Model/policy version tracking (§12.6)."""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class VersionSnapshot:
    """The exact model/policy/prompt versions behind a decision (§12.6)."""

    classifier_version: str
    propensity_model_version: str
    uplift_model_version: str
    policy_version: str
    prompt_version: str
    message_template_version: str


class VersionTracker:
    """Attaches a VersionSnapshot to every decision (§12.6)."""

    def current(self) -> VersionSnapshot:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §12.6")

    def snapshot_for_decision(self, **overrides: str) -> VersionSnapshot:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §12.6")