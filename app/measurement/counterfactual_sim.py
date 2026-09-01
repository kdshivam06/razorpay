"""Counterfactual strategy simulator (§12.3)."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping


DEFAULT_STRATEGIES = {
    "Fixed Rules": {
        "contact_rate": 1.0,
        "uplift_multiplier": 0.72,
        "cost_per_contact_paise": 130,
        "base_contacts_per_case": 3.0,
    },
    "AI Optimized Recovery": {
        "contact_rate": 0.72,
        "uplift_multiplier": 1.0,
        "cost_per_contact_paise": 100,
        "base_contacts_per_case": 2.0,
    },
    "Human Only": {
        "contact_rate": 0.34,
        "uplift_multiplier": 0.88,
        "cost_per_contact_paise": 2200,
        "base_contacts_per_case": 1.0,
    },
}


@dataclasses.dataclass(frozen=True)
class StrategyComparison:
    """Comparison across strategies; clearly labelled as synthetic (§12.3)."""

    strategies: dict[str, dict]
    best_strategy: str


class CounterfactualSimulator:
    """Compares 'Fixed Rule Dunning vs AI Optimized vs Human Only' on
    synthetic simulations. Numbers are explicitly synthetic (§12.3)."""

    def run(
        self,
        strategies: dict[str, dict],
        synthetic_cases: list[dict],
        outcome_model: str = "ground_truth",
    ) -> StrategyComparison:
        strategy_configs = strategies or DEFAULT_STRATEGIES
        results = {
            name: _simulate_strategy(name, config, synthetic_cases, outcome_model)
            for name, config in strategy_configs.items()
        }
        best_strategy = max(
            results,
            key=lambda name: results[name]["net_recovered_paise"],
            default="",
        )
        return StrategyComparison(strategies=results, best_strategy=best_strategy)


def _simulate_strategy(
    name: str,
    config: Mapping[str, object],
    synthetic_cases: list[dict],
    outcome_model: str,
) -> dict:
    contact_rate = _bounded_float(config.get("contact_rate", 1.0), 0.0, 1.0)
    uplift_multiplier = _bounded_float(config.get("uplift_multiplier", 1.0), 0.0, 3.0)
    cost_per_contact = max(0, int(config.get("cost_per_contact_paise", 0)))
    contacts_per_case = max(0.0, float(config.get("base_contacts_per_case", 1.0)))
    fixed_cost = max(0, int(config.get("fixed_cost_paise", 0)))

    gross_recovery = 0.0
    natural_recovery = 0.0
    incremental_recovery = 0.0
    contacts = 0.0

    for case in synthetic_cases:
        amount = _case_amount_paise(case)
        natural_probability = _truth_float(case, "true_natural_probability", 0.0)
        best_uplift = max(0.0, _best_action_uplift(case))
        treated_uplift = best_uplift * uplift_multiplier * contact_rate
        recovery_probability = min(1.0, natural_probability + treated_uplift)

        natural_recovery += amount * natural_probability
        incremental_recovery += amount * treated_uplift
        gross_recovery += amount * recovery_probability
        contacts += contacts_per_case * contact_rate

    contact_count = int(round(contacts))
    cost = fixed_cost + contact_count * cost_per_contact
    gross = int(round(gross_recovery))
    natural = int(round(natural_recovery))
    incremental = int(round(incremental_recovery))

    return {
        "strategy": name,
        "synthetic": True,
        "outcome_model": outcome_model,
        "case_count": len(synthetic_cases),
        "recovery_paise": gross,
        "natural_recovered_paise": natural,
        "incremental_recovered_paise": incremental,
        "cost_paise": cost,
        "contacts": contact_count,
        "net_recovered_paise": gross - cost,
        "recovery_per_contact_paise": int(round(gross / contact_count))
        if contact_count
        else 0,
        "recovery_per_cost_rupee": round(gross / cost, 4) if cost else None,
    }


def _case_amount_paise(case: Mapping[str, object]) -> int:
    for key in ("outstanding_amount_paise", "amount_paise", "amount"):
        if key in case and case[key] not in (None, ""):
            return max(0, int(float(case[key])))
    return 0


def _truth_float(case: Mapping[str, object], key: str, default: float) -> float:
    if key in case and case[key] not in (None, ""):
        return float(case[key])
    truth = case.get("ground_truth")
    if isinstance(truth, Mapping) and key in truth:
        return float(truth[key])
    return default


def _best_action_uplift(case: Mapping[str, object]) -> float:
    uplift = case.get("true_action_uplift")
    if not isinstance(uplift, Mapping):
        truth = case.get("ground_truth")
        uplift = truth.get("true_action_uplift") if isinstance(truth, Mapping) else None
    if isinstance(uplift, Mapping):
        candidates = [
            float(value)
            for action, value in uplift.items()
            if str(action).upper() not in {"NO_ACTION", "WAIT", "BLOCK"}
        ]
        return max(candidates, default=0.0)
    return _truth_float(case, "true_uplift", 0.0)


def _bounded_float(value: object, lower: float, upper: float) -> float:
    return min(upper, max(lower, float(value)))
