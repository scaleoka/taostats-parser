"""Validate each dataset and join coverage without assuming contiguous IDs."""

from collections import Counter
from dataclasses import dataclass, field, fields
from datetime import datetime, timedelta
from decimal import Decimal

from .models import Metadata, SubnetSnapshot, ValidatorMetrics

SCHEMAS = {"subnets": SubnetSnapshot, "metadata": Metadata, "validator_metrics": ValidatorMetrics}


@dataclass
class ValidationResult:
    status: str = "ok"
    errors: list[str] = field(default_factory=list)
    record_counts: dict[str, int] = field(default_factory=dict)
    missing_ids: dict[str, list[int]] = field(default_factory=dict)
    duplicate_ids: dict[str, list[int]] = field(default_factory=dict)
    unexpected_ids: dict[str, list[int]] = field(default_factory=dict)


def validate(datasets: dict, expected_ids: set[int] | None = None) -> ValidationResult:
    result = ValidationResult()
    coverage = {}
    if expected_ids is not None and any(type(i) is not int or i < 0 for i in expected_ids):
        result.errors.append("Expected IDs must be nonnegative integers")
        expected_ids = None
    for name, schema in SCHEMAS.items():
        records = datasets.get(name, [])
        result.record_counts[name] = len(records)
        if not records:
            result.errors.append(f"{name}: empty dataset")
        ids = []
        for index, record in enumerate(records):
            if not isinstance(record, schema):
                result.errors.append(f"{name}[{index}]: invalid record type")
                continue
            if type(record.netuid) is not int or record.netuid < 0:
                result.errors.append(f"{name}[{index}]: invalid netuid")
            else:
                ids.append(record.netuid)
            try:
                moment = datetime.fromisoformat(record.collected_at_utc.replace("Z", "+00:00"))
                if moment.utcoffset() != timedelta(0):
                    raise ValueError("Timestamp must be UTC")
            except (ValueError, TypeError, AttributeError):
                result.errors.append(f"{name}[{index}]: invalid UTC timestamp")
            for column in fields(record):
                value = getattr(record, column.name)
                if "Decimal" not in str(column.type) or value is None:
                    continue
                if not isinstance(value, Decimal) or not value.is_finite():
                    result.errors.append(f"{name}[{index}].{column.name}: invalid number")
                elif not column.name.startswith("price_change_") and value < 0:
                    result.errors.append(f"{name}[{index}].{column.name}: negative value")
            if isinstance(record, ValidatorMetrics):
                for color in ("orange", "green"):
                    low = getattr(record, f"incentive_{color}_min")
                    high = getattr(record, f"incentive_{color}_max")
                    if (low is None) != (high is None):
                        result.errors.append(f"{name}[{index}]: incomplete {color} range")
                    if isinstance(low, Decimal) and isinstance(high, Decimal):
                        if low.is_finite() and high.is_finite() and low > high:
                            result.errors.append(f"{name}[{index}]: reversed {color} range")
        coverage[name] = set(ids)
        duplicates = sorted(i for i, count in Counter(ids).items() if count > 1)
        result.duplicate_ids[name] = duplicates
        if duplicates:
            result.errors.append(f"{name}: duplicate IDs {duplicates}")
    # Without an external inventory, union coverage still detects partial joins.
    expected = expected_ids if expected_ids is not None else set().union(*coverage.values())
    for name, ids in coverage.items():
        missing, unexpected = sorted(expected - ids), sorted(ids - expected)
        result.missing_ids[name], result.unexpected_ids[name] = missing, unexpected
        if missing or unexpected:
            result.errors.append(f"{name}: missing IDs {missing}; unexpected IDs {unexpected}")
    extra = set(datasets) - set(SCHEMAS)
    if extra:
        result.errors.append(f"Unknown datasets: {sorted(extra)}")
    result.status = "error" if result.errors else "ok"
    return result
