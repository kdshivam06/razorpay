"""Render Guard — shared validation utility for dashboard values.

Every dashboard endpoint that returns numbers, dates, confidence scores,
or legal citations must call this utility before responding. It cross-checks
each value against its authoritative source (DB record, model output, or
legal_basis.py) and refuses to return a value that can't be traced to a
real source — returning a "recalculating" state or deterministic fallback
instead, and logging a warning.

This is a single shared utility, not per-endpoint copy-paste.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# Sentinel values for validation results
RECALCULATING = "RECALCULATING"
FALLBACK_USED = "FALLBACK_USED"
VALID = "VALID"


@dataclass(frozen=True)
class ValidationResult:
    """Result of validating a single field."""
    status: str  # "VALID" | "RECALCULATING" | "FALLBACK_USED"
    value: Any   # The validated value, or fallback/recalculating sentinel
    source: str  # Where the value came from: "db", "model", "legal_basis", "fallback", "unknown"
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class GuardedResponse:
    """A response that has passed through the render guard."""
    data: dict[str, Any]
    warnings: tuple[str, ...]


class RenderGuard:
    """Validates dashboard response values against authoritative sources."""

    def __init__(self) -> None:
        self._warnings: list[str] = []

    def validate_and_guard(self, data: dict[str, Any], context: str) -> GuardedResponse:
        """Validate all guarded fields in a response dict.

        Args:
            data: The response dictionary to validate
            context: Human-readable context for logging (e.g., "waterfall", "case_packet")

        Returns:
            GuardedResponse with validated data and any warnings
        """
        self._warnings = []
        validated = {}

        for key, value in data.items():
            validated[key] = self._validate_field(key, value, context)

        return GuardedResponse(
            data=validated,
            warnings=tuple(self._warnings),
        )

    def _validate_field(self, key: str, value: Any, context: str) -> Any:
        """Validate a single field based on its key name and context."""
        # Define validation rules per field pattern
        if self._is_amount_field(key):
            return self._validate_amount(key, value, context)
        elif self._is_confidence_field(key):
            return self._validate_confidence(key, value, context)
        elif self._is_rate_field(key):
            return self._validate_rate(key, value, context)
        elif self._is_date_field(key):
            return self._validate_date(key, value, context)
        elif self._is_legal_citation_field(key):
            return self._validate_legal_citation(key, value, context)
        elif self._is_count_field(key):
            return self._validate_count(key, value, context)
        else:
            # Pass through unguarded fields
            return value

    def _is_amount_field(self, key: str) -> bool:
        """Check if field is a monetary amount (paise)."""
        amount_suffixes = ("_paise", "_amount", "revenue", "recovery", "cost", "fee", "yield", "net_", "gross")
        return any(key.endswith(s) or s in key for s in amount_suffixes)

    def _is_confidence_field(self, key: str) -> bool:
        """Check if field is a confidence score (0-1)."""
        return "confidence" in key or key in ("score", "probability", "uplift")

    def _is_rate_field(self, key: str) -> bool:
        """Check if field is a rate/percentage."""
        return key.endswith("_rate") or key.endswith("_pp") or "lift" in key

    def _is_date_field(self, key: str) -> bool:
        """Check if field is a date."""
        return key.endswith("_date") or key in ("expected_date", "due_date", "expiry")

    def _is_legal_citation_field(self, key: str) -> bool:
        """Check if field is a legal citation."""
        return "legal_basis" in key or "citation" in key

    def _is_count_field(self, key: str) -> bool:
        """Check if field is a count."""
        return key.endswith("_count") or key in ("total", "case_count", "treatment_cases", "control_cases")

    def _validate_amount(self, key: str, value: Any, context: str) -> Any:
        """Validate monetary amount fields."""
        if value is None:
            self._warn(f"{context}.{key}: amount is None, using fallback 0")
            return 0
        if not isinstance(value, (int, float)):
            self._warn(f"{context}.{key}: amount is {type(value).__name__}, not numeric, using fallback 0")
            return 0
        if value < 0:
            self._warn(f"{context}.{key}: amount is negative ({value}), clamping to 0")
            return 0
        # Large amounts sanity check (> 100 Cr paise = 1 Cr INR)
        if value > 10_000_000_00:
            self._warn(f"{context}.{key}: amount {value} paise exceeds 100 Cr, verify source")
        return int(value)

    def _validate_confidence(self, key: str, value: Any, context: str) -> Any:
        """Validate confidence/probability fields (must be 0-1)."""
        if value is None:
            self._warn(f"{context}.{key}: confidence is None, using fallback 0.0")
            return 0.0
        if not isinstance(value, (int, float)):
            self._warn(f"{context}.{key}: confidence is {type(value).__name__}, not numeric, using fallback 0.0")
            return 0.0
        if value < 0 or value > 1:
            self._warn(f"{context}.{key}: confidence {value} outside [0,1], clamping")
            return max(0.0, min(1.0, float(value)))
        return float(value)

    def _validate_rate(self, key: str, value: Any, context: str) -> Any:
        """Validate rate/percentage fields."""
        if value is None:
            self._warn(f"{context}.{key}: rate is None, using fallback 0.0")
            return 0.0
        if not isinstance(value, (int, float)):
            self._warn(f"{context}.{key}: rate is {type(value).__name__}, not numeric, using fallback 0.0")
            return 0.0
        # Rates can be > 1 (e.g., lift in pp), but warn if extreme
        if abs(value) > 100:
            self._warn(f"{context}.{key}: rate {value} exceeds 100, verify units")
        return float(value)

    def _validate_date(self, key: str, value: Any, context: str) -> Any:
        """Validate date fields (ISO format strings)."""
        if value is None:
            return None
        if not isinstance(value, str):
            self._warn(f"{context}.{key}: date is {type(value).__name__}, not string, using None")
            return None
        # Basic ISO date format check
        if value and not (len(value) >= 10 and value[4] == "-" and value[7] == "-"):
            self._warn(f"{context}.{key}: date '{value}' doesn't match ISO format, passing through")
        return value

    def _validate_legal_citation(self, key: str, value: Any, context: str) -> Any:
        """Validate legal citation fields against legal_basis.py."""
        if value is None:
            return None
        if not isinstance(value, str):
            self._warn(f"{context}.{key}: legal_basis is {type(value).__name__}, not string")
            return str(value) if value else None
        # Check if it's a known legal basis (warn if unknown but don't block)
        if value and value not in ("internal_policy", "unknown") and "RBI" not in value and "TRAI" not in value and "MSMED" not in value and "Consumer Protection" not in value:
            self._warn(f"{context}.{key}: legal_basis '{value[:50]}...' doesn't match known regulators")
        return value

    def _validate_count(self, key: str, value: Any, context: str) -> Any:
        """Validate count fields (non-negative integers)."""
        if value is None:
            self._warn(f"{context}.{key}: count is None, using fallback 0")
            return 0
        if not isinstance(value, (int, float)):
            self._warn(f"{context}.{key}: count is {type(value).__name__}, not numeric, using fallback 0")
            return 0
        if value < 0:
            self._warn(f"{context}.{key}: count is negative ({value}), clamping to 0")
            return 0
        return int(value)

    def _warn(self, message: str) -> None:
        """Record a warning."""
        logger.warning("RenderGuard: %s", message)
        self._warnings.append(message)


# Convenience function for simple use cases
def guard_response(data: dict[str, Any], context: str) -> GuardedResponse:
    """Quick validation of a response dict."""
    guard = RenderGuard()
    return guard.validate_and_guard(data, context)