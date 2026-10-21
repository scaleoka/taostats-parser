"""Pure offline extraction and project calculations; no model or UI equivalence.

Inputs are explicit page sequences or CompletedQuery for one endpoint/filter set/netuid.
Field names are observed API names; numeric units and timestamp semantics remain
unconfirmed. See docs/taostats-api-mapping.md for the conservative local contract.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from types import MappingProxyType
from typing import Mapping

from .api_v1 import Budget, CompletedQuery, PageMerger, ResponseError

_METAGRAPH = "/v1/subnets/metagraph"
_FLAGS = frozenset(("active", "validator_permit", "is_immune", "is_owner_hotkey", "in_danger"))
_FIELDS = {
    "/v1/subnets/identities": {
        "subnet_name": "text",
        "discord": "text",
        "github_repo": "text",
    },
    "/v1/subnets/metrics": {
        "registration_timestamp": "raw",
        "registration_cost": "decimal",
        "emission": "decimal",
    },
    "/v1/subnets/pools": dict.fromkeys(
        ("price", "market_cap", "liquidity", "root_prop"), "decimal"
    ),
    "/v1/subnets/pools/aggregate": {
        **dict.fromkeys(
            (
                "tao_volume_24_hr",
                "alpha_volume_24_hr",
                "price_change_1_hour",
                "price_change_1_day",
                "price_change_1_week",
                "price_change_1_month",
            ),
            "decimal",
        ),
        "sentiment": "raw",
    },
    "/v1/subnets/metagraph/aggregate": {
        **dict.fromkeys(
            ("max_incentive", "max_immune_incentive", "min_non_immune_incentive"), "nonnegative"
        ),
        "immune_count": "integer",
    },
    _METAGRAPH: {
        **dict.fromkeys(sorted(_FLAGS), "boolean"),
        "validator_trust": "nonnegative",
        "incentive": "nonnegative",
    },
}


class MappingError(ResponseError):
    """Input cannot support a complete, unambiguous offline result."""


def _integer(value, name):
    if type(value) is not int or value < 0:
        raise MappingError(f"{name} must be a nonnegative integer")
    return value


def _decimal(value):
    if type(value) is str:
        if (
            len(value) > 128
            or re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", value) is None
        ):
            raise MappingError("invalid decimal literal")
        _, marker, exponent = value.lower().partition("e")
        if marker and not -308 <= int(exponent) <= 308:
            raise MappingError("decimal exponent out of range")
        number = Decimal(value)
    elif type(value) is int:
        if value.bit_length() > 425:  # At most 128 decimal digits; check before conversion.
            raise MappingError("integer magnitude out of range")
        number = Decimal(value)
    elif type(value) is Decimal:
        number = value
    else:
        raise MappingError("expected Decimal, integer or decimal text; floats are not accepted")
    if not number.is_finite():
        raise MappingError("nonfinite decimal")
    parts = number.as_tuple()
    if len(parts.digits) > 128 or not -436 <= parts.exponent <= 308:
        raise MappingError("decimal precision or exponent out of range")
    if number and not -308 <= number.adjusted() <= 308:
        raise MappingError("decimal magnitude out of range")
    return number


def _value(value, kind, name):
    if value is None:
        return None
    if kind == "boolean":
        if type(value) is not bool:
            raise MappingError(f"{name} must be boolean")
    elif kind == "integer":
        value = _integer(value, name)
    elif kind in ("decimal", "nonnegative"):
        value = _decimal(value)
        if kind == "nonnegative" and value < 0:
            raise MappingError(f"{name} must be nonnegative")
    elif kind == "text":
        if type(value) is not str:
            raise MappingError(f"{name} must be text")
    elif type(value) is not str:
        # Preserve unknown timestamp/sentiment representations; never infer units.
        if type(value) is int:
            _decimal(value)
        elif type(value) is Decimal:
            _decimal(value)
        else:
            raise MappingError(f"{name} must be a scalar text or exact number")
    return value


@dataclass(frozen=True)
class APIRecord:
    netuid: int
    uid: int | None
    block_number: int | None
    source_timestamp: str | int | Decimal | None
    fields: Mapping[str, object]


@dataclass(frozen=True)
class NormalizedBatch:
    endpoint: str
    netuid: int
    block_number: int | None
    loaded_at_utc: str | None
    records: tuple[APIRecord, ...]


@dataclass(frozen=True)
class IncentiveBounds:
    """Project result for explicit filters, not Taostats orange/green categories."""

    minimum: Decimal | None
    maximum: Decimal | None
    count: int


def normalize_pages(endpoint, pages, *, netuid, expected_block=None, loaded_at_utc=None):
    """Accept envelopes or CompletedQuery, then extract only observed fields.

    CompletedQuery skips pagination replay; all content checks remain in force.

    Non-metagraph queries conservatively require one row per netuid. Metagraph
    rows require block_number for subsequent calculations. Other endpoints may
    omit blocks entirely; mixed missing/known or differing blocks are rejected.
    """
    if type(endpoint) is not str or endpoint not in _FIELDS:
        raise MappingError("unsupported endpoint")
    _integer(netuid, "netuid")
    if expected_block is not None:
        _integer(expected_block, "expected_block")
    if loaded_at_utc is not None:
        if not isinstance(loaded_at_utc, datetime) or loaded_at_utc.utcoffset() is None:
            raise MappingError("loaded_at_utc must be a timezone-aware datetime")
        loaded_at_utc = loaded_at_utc.astimezone(timezone.utc).isoformat()
    if type(pages) is CompletedQuery:
        if pages.endpoint != endpoint:
            raise MappingError("completed query endpoint differs from requested endpoint")
        rows = pages.rows
    else:
        if type(pages) not in (list, tuple) or not pages:
            raise MappingError("CompletedQuery or nonempty list/tuple of page envelopes required")
        merger = PageMerger(
            endpoint,
            Budget(len(pages), len(pages)),
            key_fields=None if endpoint == _METAGRAPH else ("netuid",),
        )
        for page in pages:
            merger.reserve_next_page()
            merger.accept(page)
        rows = merger.finish()
    # A completed non-metagraph query may have used other integer key_fields.
    # Preserve this mapper's narrower one-row-per-subnet content contract.
    if endpoint != _METAGRAPH and len(rows) > 1:
        raise MappingError("multiple records for one subnet are not supported")
    records = []
    blocks = set()
    for row in rows:
        if _integer(row.get("netuid"), "netuid") != netuid:
            raise MappingError("netuid differs from the requested subnet")
        uid = _integer(row.get("uid"), "uid") if endpoint == _METAGRAPH else None
        has_source = endpoint != "/v1/subnets/identities"
        block = row.get("block_number") if has_source else None
        if block is not None:
            block = _integer(block, "block_number")
        if endpoint == _METAGRAPH and block is None:
            raise MappingError("metagraph block_number is required")
        if expected_block is not None and block != expected_block:
            raise MappingError("block_number differs from expected_block")
        blocks.add(block)
        fields = {
            name: _value(row.get(name), kind, name) for name, kind in _FIELDS[endpoint].items()
        }
        timestamp = _value(row.get("timestamp"), "raw", "timestamp") if has_source else None
        records.append(APIRecord(netuid, uid, block, timestamp, MappingProxyType(fields)))
    if len(blocks) > 1:
        raise MappingError("block_number differs or is missing across records")
    return NormalizedBatch(
        endpoint, netuid, next(iter(blocks), None), loaded_at_utc, tuple(records)
    )


def _selected(pages, netuid, expected_block, filters):
    if not isinstance(filters, Mapping) or not filters:
        raise MappingError("explicit nonempty boolean filters required")
    for name, value in filters.items():
        if name not in _FLAGS or type(value) is not bool:
            raise MappingError("unsupported boolean filter")
    batch = normalize_pages(_METAGRAPH, pages, netuid=netuid, expected_block=expected_block)
    selected = []
    for record in batch.records:
        # Check every filter even when a preceding predicate does not match.
        if any(record.fields[name] is None for name in filters):
            raise MappingError("required filter field is missing")
        if all(record.fields[name] is value for name, value in filters.items()):
            selected.append(record)
    return selected


def _metric_values(records, name):
    values = [record.fields[name] for record in records]
    if any(value is None for value in values):
        raise MappingError("selected record is missing the required metric")
    return values


def vtrust_avg(pages, *, netuid, expected_block=None):
    """Project mean of active+permit neurons, including zeros; empty means None.

    Sum exactly with a bounded-data-derived decimal precision (at least 50), then
    divide using ROUND_HALF_EVEN. Repeating means necessarily round; inputs do not.
    Caller Decimal context does not affect this calculation.
    """
    records = _selected(pages, netuid, expected_block, {"active": True, "validator_permit": True})
    values = _metric_values(records, "validator_trust")
    if not values:
        return None
    precision = max(
        50,
        max(value.adjusted() for value in values)
        - min(value.as_tuple().exponent for value in values)
        + len(str(len(values)))
        + 2,
    )
    with localcontext(Context(prec=precision, rounding=ROUND_HALF_EVEN)):
        return sum(values, Decimal(0)) / len(values)


def incentive_bounds(pages, *, netuid, filters, expected_block=None):
    """Min/max by caller-supplied predicates; no implicit miner/owner/UI mapping."""
    records = _selected(pages, netuid, expected_block, filters)
    values = _metric_values(records, "incentive")
    return IncentiveBounds(min(values, default=None), max(values, default=None), len(values))
