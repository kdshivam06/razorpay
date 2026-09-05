"""Recovery economics engine — cost-benefit per action (§6.2).

Implements: Net Economic Value = Expected Gross Recovery − Communication Cost
            − Operational Cost − Risk Penalty − CX Penalty

All monetary values are integer paise.
"""

from __future__ import annotations

from app.contracts import Action, CandidateAction
from app.revenue_risk.uplift_model import UpliftEstimates

# ---------------------------------------------------------------------------
# Cost tables — §6.2 examples converted to paise
# ---------------------------------------------------------------------------

# Communication cost per channel (paise).  Values drawn from §6.2:
#   SMS ≈ ₹0.05 = 5p, Email ≈ ₹0.02 = 2p, WhatsApp ≈ ₹0.10 = 10p,
#   Voice ≈ ₹1.00 = 100p, Payment Link creation ≈ ₹0.50 = 50p.
_DEFAULT_COMM_COST: dict[Action, int] = {
    Action.NO_ACTION: 0,
    Action.WAIT: 0,
    Action.RETRY_SAME_METHOD: 10,
    Action.RETRY_ALTERNATE_METHOD: 20,
    Action.SEND_PAYMENT_LINK: 50,
    Action.SEND_SMS: 5,
    Action.SEND_EMAIL: 2,
    Action.SEND_WHATSAPP: 10,
    Action.VOICE_CALL: 100,
    Action.REQUEST_PAYMENT_METHOD_UPDATE: 50,
    Action.OFFER_PARTIAL_PAYMENT: 50,
    Action.CREATE_PTP: 0,
    Action.HUMAN_ESCALATION: 0,
    Action.BLOCK: 0,
    Action.WRITE_OFF: 0,
}

# Operational cost represents human-time overhead (paise).
_DEFAULT_OP_COST: dict[Action, int] = {
    Action.NO_ACTION: 0,
    Action.WAIT: 0,
    Action.RETRY_SAME_METHOD: 0,
    Action.RETRY_ALTERNATE_METHOD: 0,
    Action.SEND_PAYMENT_LINK: 0,
    Action.SEND_SMS: 0,
    Action.SEND_EMAIL: 0,
    Action.SEND_WHATSAPP: 0,
    Action.VOICE_CALL: 500,  # voice requires review effort
    Action.REQUEST_PAYMENT_METHOD_UPDATE: 0,
    Action.OFFER_PARTIAL_PAYMENT: 200,
    Action.CREATE_PTP: 100,
    Action.HUMAN_ESCALATION: 1900,  # ≈ ₹19 human time per §12.3
    Action.BLOCK: 0,
    Action.WRITE_OFF: 0,
}

# Customer-experience penalty: cost of annoying the customer (paise).
# Higher for intrusive channels. §6.2: SMS CX ₹1 = 100p, Voice CX ₹7 = 700p.
_DEFAULT_CX_PENALTY: dict[Action, int] = {
    Action.NO_ACTION: 0,
    Action.WAIT: 0,
    Action.RETRY_SAME_METHOD: 20,
    Action.RETRY_ALTERNATE_METHOD: 30,
    Action.SEND_PAYMENT_LINK: 50,
    Action.SEND_SMS: 100,
    Action.SEND_EMAIL: 30,
    Action.SEND_WHATSAPP: 80,
    Action.VOICE_CALL: 700,
    Action.REQUEST_PAYMENT_METHOD_UPDATE: 60,
    Action.OFFER_PARTIAL_PAYMENT: 40,
    Action.CREATE_PTP: 20,
    Action.HUMAN_ESCALATION: 0,
    Action.BLOCK: 0,
    Action.WRITE_OFF: 0,
}

# Risk penalty: expected cost of bad outcomes (disputes, fraud triggers).
# Only applied to actions that execute financial operations.
_DEFAULT_RISK_PENALTY: dict[Action, int] = {
    Action.NO_ACTION: 0,
    Action.WAIT: 0,
    Action.RETRY_SAME_METHOD: 50,
    Action.RETRY_ALTERNATE_METHOD: 80,
    Action.SEND_PAYMENT_LINK: 30,
    Action.SEND_SMS: 10,
    Action.SEND_EMAIL: 5,
    Action.SEND_WHATSAPP: 10,
    Action.VOICE_CALL: 40,
    Action.REQUEST_PAYMENT_METHOD_UPDATE: 20,
    Action.OFFER_PARTIAL_PAYMENT: 20,
    Action.CREATE_PTP: 10,
    Action.HUMAN_ESCALATION: 0,
    Action.BLOCK: 0,
    Action.WRITE_OFF: 0,
}


class CommunicationCostTable:
    """Per-channel cost lookup used by the economics engine (§6.2)."""

    def __init__(
        self,
        overrides: dict[str, int] | None = None,
    ) -> None:
        self._table = dict(_DEFAULT_COMM_COST)
        if overrides:
            for key, value in overrides.items():
                try:
                    action = Action(key) if not isinstance(key, Action) else key
                    self._table[action] = value
                except ValueError:
                    pass

    def cost_paise(self, channel: str) -> int:
        """Return communication cost in paise for a channel / action name."""
        try:
            return self._table[Action(channel)]
        except ValueError:
            return 0


class RecoveryEconomics:
    """Computes Net Economic Value of each candidate action:

    Net = Expected Gross Recovery − Communication Cost − Operational Cost
          − Risk Penalty − CX Penalty        (§6.2)
    """

    def __init__(
        self,
        comm_costs: CommunicationCostTable | None = None,
        op_costs: dict[Action, int] | None = None,
        cx_penalties: dict[Action, int] | None = None,
        risk_penalties: dict[Action, int] | None = None,
    ) -> None:
        self._comm = comm_costs or CommunicationCostTable()
        self._op = op_costs or dict(_DEFAULT_OP_COST)
        self._cx = cx_penalties or dict(_DEFAULT_CX_PENALTY)
        self._risk = risk_penalties or dict(_DEFAULT_RISK_PENALTY)

    def net_economic_value(
        self,
        expected_recovery: int,
        communication_cost: int,
        operational_cost: int,
        risk_penalty: int,
        cx_penalty: int,
    ) -> int:
        """The §1.1 inner expression, all paise:

        ExpectedIncrementalRecovery(a) − ActionCost(a)
        − CustomerExperiencePenalty(a) − RiskPenalty(a)
        """
        return (
            expected_recovery
            - communication_cost
            - operational_cost
            - risk_penalty
            - cx_penalty
        )

    def score_candidates(
        self,
        estimates: UpliftEstimates,
        amount_remaining_paise: int,
    ) -> list[CandidateAction]:
        """Build a scored CandidateAction for every action in the Action enum.

        Each candidate's expected_recovery_paise uses uplift-based incremental
        recovery: uplift × amount_remaining.  NO_ACTION has uplift=0 and all
        costs=0, making it the zero-baseline that every other action must beat.
        """
        candidates: list[CandidateAction] = []

        for action in Action:
            uplift = estimates.per_action_uplift.get(action, 0.0)
            # Incremental expected recovery for this action
            expected_recovery = max(0, round(uplift * amount_remaining_paise))

            comm = self._comm.cost_paise(action.value)
            op = self._op.get(action, 0)
            cx = self._cx.get(action, 0)
            risk = self._risk.get(action, 0)

            score = float(
                self.net_economic_value(expected_recovery, comm, op, risk, cx)
            )

            candidates.append(
                CandidateAction(
                    action=action,
                    expected_recovery_paise=expected_recovery,
                    communication_cost_paise=comm,
                    operational_cost_paise=op,
                    risk_penalty_paise=risk,
                    cx_penalty_paise=cx,
                    economic_score=score,
                )
            )

        return candidates
