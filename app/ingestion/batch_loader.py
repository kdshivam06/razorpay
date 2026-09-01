"""Loads data/synthetic_batch.csv against a documented schema (§15)."""

from __future__ import annotations

import csv
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BATCH_PATH = PROJECT_ROOT / "data" / "synthetic_batch.csv"

# ---------------------------------------------------------------------------
# Documented CSV schema (the generator in data/generate_synthetic.py MUST emit
# a header row with exactly these column names; order is free, extras allowed).
#
#   column                      type       description
#   --------------------------  ---------  ------------------------------
#   event_id                    str        unique event id (e.g. evt_...)
#   event_type                  str        payment.failed / payment.captured /
#                                          invoice.paid / mandate.revoked ...
#   customer_id                 str        customer identifier (cus_...)
#   merchant_id                 str        merchant identifier (mer_...)
#   obligation_id               str        canonical obligation id (OBL_...)
#   obligation_type             str        payment | invoice | subscription |
#                                          mandate
#   amount_paise                int        original amount in paise (>= 0)
#   currency                    str        ISO-4217, default INR
#   payment_method              str        card | upi | netbanking | wallet |
#                                          emandate
#   failure_reason              str        root-cause category from §15.5
#                                          (insufficient_funds, expired_card,
#                                          bank_timeout, gateway_error,
#                                          checkout_abandoned, mandate_revoked,
#                                          overdue_invoice, partial_payment,
#                                          risk_block, dispute_filed,
#                                          intl_decline, unknown_error ...)
#   created_at                  int        event epoch seconds (bare ISO-8601
#                                          also accepted and converted)
#   persona                     str        persona id P1..P10 (§15.2)
#   true_natural_probability    float      hidden ground truth 0..1 (§15.3)
#   true_action_uplift          float      hidden ground truth (>= 0) (§15.3)
#   true_payment_time           int        hidden days-to-payment (§15.3)
#   true_fraud_state            int        0 | 1 hidden ground truth (§15.3)
#   true_dispute_state          int        0 | 1 hidden ground truth (§15.3)
# ---------------------------------------------------------------------------

SCHEMA: dict[str, str] = {
    "event_id": "str",
    "event_type": "str",
    "customer_id": "str",
    "merchant_id": "str",
    "obligation_id": "str",
    "obligation_type": "str",
    "amount_paise": "int",
    "currency": "str",
    "payment_method": "str",
    "failure_reason": "str",
    "created_at": "datetime",
    "persona": "str",
    "true_natural_probability": "float",
    "true_action_uplift": "float",
    "true_payment_time": "int",
    "true_fraud_state": "int",
    "true_dispute_state": "int",
}

REQUIRED_COLUMNS = frozenset(SCHEMA)

OBLIGATION_TYPES = frozenset({"payment", "invoice", "subscription", "mandate"})
PERSONAS = frozenset({f"P{i}" for i in range(1, 11)})


class BatchLoaderError(Exception):
    """Raised for missing files, bad headers, or invalid rows."""


@dataclass
class BatchLoadResult:
    records: list[dict]
    path: Path
    row_count: int
    errors: list[str]


def load_synthetic_batch(
    path: str | Path | None = None, *, fail_on_error: bool = False
) -> BatchLoadResult:
    """Load and validate every row of the synthetic batch CSV.

    Raises BatchLoaderError if the file is missing or the header is invalid.
    Invalid rows are collected into `errors` unless `fail_on_error` is set.
    """
    batch_path = Path(path) if path is not None else DEFAULT_BATCH_PATH
    if not batch_path.exists():
        raise BatchLoaderError(
            f"Batch file not found: {batch_path}. Generate it first via "
            "data/generate_synthetic.py."
        )

    try:
        with batch_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = [name.strip() for name in (reader.fieldnames or [])]
            rows = [dict(row) for row in reader]
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise BatchLoaderError(f"Cannot read batch file {batch_path}: {exc}") from exc

    missing = REQUIRED_COLUMNS - set(fieldnames)
    if missing:
        raise BatchLoaderError(
            f"Batch header missing required columns: {sorted(missing)}"
        )

    records: list[dict] = []
    errors: list[str] = []
    for index, raw in enumerate(rows, start=2):  # header row is line 1
        if _is_blank(raw):
            continue
        try:
            records.append(_validate_and_coerce(raw, index))
        except BatchLoaderError as exc:
            if fail_on_error:
                raise BatchLoaderError(
                    f"{batch_path}: invalid row {index}: {exc}"
                ) from exc
            errors.append(str(exc))

    return BatchLoadResult(
        records=records, path=batch_path, row_count=len(rows), errors=errors
    )


def _is_blank(row: dict) -> bool:
    return not any(str(value).strip() for value in row.values())


def _validate_and_coerce(raw: dict, row_number: int) -> dict:
    row = {str(key).strip(): str(value).strip() for key, value in raw.items()}
    for column in REQUIRED_COLUMNS:
        if row.get(column, "") == "":
            raise BatchLoaderError(
                f"row {row_number}: missing required column '{column}'"
            )

    amounts = [
        _coerce(row, row_number, "amount_paise", "int", check=lambda v: v >= 0),
        _coerce(row, row_number, "true_payment_time", "int", check=lambda v: v >= 0),
        _coerce(row, row_number, "true_fraud_state", "int", check=lambda v: v in (0, 1)),
        _coerce(
            row, row_number, "true_dispute_state", "int", check=lambda v: v in (0, 1)
        ),
        _coerce(
            row,
            row_number,
            "true_natural_probability",
            "float",
            check=lambda v: 0.0 <= v <= 1.0,
        ),
        _coerce(
            row,
            row_number,
            "true_action_uplift",
            "float",
            check=lambda v: v >= 0.0,
        ),
        _coerce(row, row_number, "created_at", "datetime"),
    ]
    (
        amount_paise,
        true_payment_time,
        true_fraud_state,
        true_dispute_state,
        true_natural_probability,
        true_action_uplift,
        created_at,
    ) = amounts

    obligation_type = row["obligation_type"]
    persona = row["persona"]
    if obligation_type not in OBLIGATION_TYPES:
        raise BatchLoaderError(
            f"row {row_number}: invalid obligation_type {obligation_type!r}"
        )
    if persona not in PERSONAS:
        raise BatchLoaderError(f"row {row_number}: invalid persona {persona!r}")

    return {
        "event_id": row["event_id"],
        "event_type": row["event_type"],
        "customer_id": row["customer_id"],
        "merchant_id": row["merchant_id"],
        "obligation_id": row["obligation_id"],
        "obligation_type": obligation_type,
        "amount_paise": amount_paise,
        "currency": row["currency"] or "INR",
        "payment_method": row["payment_method"],
        "failure_reason": row["failure_reason"],
        "created_at": created_at,
        "persona": persona,
        "true_natural_probability": true_natural_probability,
        "true_action_uplift": true_action_uplift,
        "true_payment_time": true_payment_time,
        "true_fraud_state": true_fraud_state,
        "true_dispute_state": true_dispute_state,
    }


def _coerce(
    row: dict,
    row_number: int,
    column: str,
    kind: str,
    check: Callable[[Any], bool] | None = None,
) -> Any:
    raw = row[column]
    try:
        if kind in ("int", "float"):
            value: Any = int(raw) if kind == "int" else float(raw)
        elif kind == "datetime":
            value = _parse_created_at(raw)
        else:
            value = raw
    except (TypeError, ValueError) as exc:
        raise BatchLoaderError(
            f"row {row_number}: column '{column}' expected {kind}, got {raw!r}"
        ) from exc
    if check is not None and not check(value):
        raise BatchLoaderError(
            f"row {row_number}: column '{column}' value {value!r} failed check"
        )
    return value


def _parse_created_at(raw: str) -> int:
    """Accept epoch-seconds ints or bare ISO-8601 strings; return epoch int."""
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except ValueError as exc:
        raise ValueError(f"not a datetime: {raw!r}") from exc