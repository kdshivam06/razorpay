"""Prevention log — 'what the agent prevented' (§13.5)."""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class PreventionRecord:
    """One logged prevention event."""

    case_id: str
    prevented_action: str
    reason: str
    amount_saved_paise: int | None


class PreventionLog:
    """Logs every action the agent PREVENTED, e.g. duplicate SMS / link /
    retry-after-payment / recovery-after-dispute / out-of-window call (§13.5).
    This is part of the agent's value, not a footnote."""

    def log(
        self,
        case_id: str,
        prevented_action: str,
        reason: str,
        amount_saved_paise: int | None = None,
    ) -> None:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §13.5")

    def summary(self) -> dict[str, object]:
        raise NotImplementedError("TODO: ML track — see implementation_plan.md §13.5")