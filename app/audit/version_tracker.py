"""Model/policy version tracking (§12.6).

Every decision stores:
  classifier_version, propensity_model_version, uplift_model_version,
  policy_version, prompt_version, message_template_version

This enables debugging "why did the agent behave differently last week?"
and experiment/model-drift analysis.
"""

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


# Default versions for the hackathon
_DEFAULTS = VersionSnapshot(
    classifier_version="classifier_v1.0_lightgbm",
    propensity_model_version="propensity_v1.0_xgboost",
    uplift_model_version="uplift_v1.0_meta_learner",
    policy_version="policy_v1.0",
    prompt_version="prompt_v1.0_gemini",
    message_template_version="templates_v1.0",
)


class VersionTracker:
    """Attaches a VersionSnapshot to every decision (§12.6).

    When a model is retrained or a policy is updated, bump the version
    here so all future decisions are tagged with the new version.
    """

    def __init__(
        self,
        classifier: str | None = None,
        propensity: str | None = None,
        uplift: str | None = None,
        policy: str | None = None,
        prompt: str | None = None,
        templates: str | None = None,
    ) -> None:
        self._classifier = classifier or _DEFAULTS.classifier_version
        self._propensity = propensity or _DEFAULTS.propensity_model_version
        self._uplift = uplift or _DEFAULTS.uplift_model_version
        self._policy = policy or _DEFAULTS.policy_version
        self._prompt = prompt or _DEFAULTS.prompt_version
        self._templates = templates or _DEFAULTS.message_template_version

    def current(self) -> VersionSnapshot:
        """Get the current version snapshot."""
        return VersionSnapshot(
            classifier_version=self._classifier,
            propensity_model_version=self._propensity,
            uplift_model_version=self._uplift,
            policy_version=self._policy,
            prompt_version=self._prompt,
            message_template_version=self._templates,
        )

    def snapshot_for_decision(self, **overrides: str) -> VersionSnapshot:
        """Get a version snapshot with optional overrides for a specific decision."""
        base = self.current()
        return VersionSnapshot(
            classifier_version=overrides.get(
                "classifier_version", base.classifier_version
            ),
            propensity_model_version=overrides.get(
                "propensity_model_version", base.propensity_model_version
            ),
            uplift_model_version=overrides.get(
                "uplift_model_version", base.uplift_model_version
            ),
            policy_version=overrides.get("policy_version", base.policy_version),
            prompt_version=overrides.get("prompt_version", base.prompt_version),
            message_template_version=overrides.get(
                "message_template_version", base.message_template_version
            ),
        )

    def bump(self, component: str, new_version: str) -> None:
        """Bump a component version."""
        attr_map = {
            "classifier": "_classifier",
            "propensity": "_propensity",
            "uplift": "_uplift",
            "policy": "_policy",
            "prompt": "_prompt",
            "templates": "_templates",
        }
        attr = attr_map.get(component)
        if attr:
            setattr(self, attr, new_version)
        else:
            raise ValueError(
                f"Unknown component '{component}'. " f"Valid: {list(attr_map.keys())}"
            )
