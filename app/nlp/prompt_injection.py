"""Prompt-injection defense (§10.5, §16.1)."""

from __future__ import annotations

import dataclasses
import enum


class InjectionVerdict(str, enum.Enum):
    BENIGN = "BENIGN"
    SUSPICIOUS = "SUSPICIOUS"
    INJECTION = "INJECTION"


@dataclasses.dataclass(frozen=True)
class InjectionAssessment:
    """Defense verdict for one piece of untrusted customer text."""

    verdict: InjectionVerdict
    score: float
    detail: str


class PromptInjectionGuard:
    """Treats customer text as UNTRUSTED. The model may classify a refund
    request, but it CANNOT execute refunds — tool permissions live outside the
    LLM (defense in depth) (§10.5, §16.1)."""

    def assess(self, text: str) -> InjectionAssessment:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §10.5")