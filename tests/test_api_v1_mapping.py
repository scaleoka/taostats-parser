"""Only synthetic explicit page sequences; conftest forbids external operations."""

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import ROUND_UP, Decimal, Inexact, localcontext
from pathlib import Path

import pytest

from taostats_pipeline import api_v1 as validation
from taostats_pipeline import api_v1_mapping as mapping
from taostats_pipeline.api_v1 import Budget, PageMerger, ResponseError
from taostats_pipeline.api_v1_mapping import (
    IncentiveBounds,
    MappingError,
    incentive_bounds,
    normalize_pages,
    vtrust_avg,
)

METAGRAPH = "/v1/subnets/metagraph"
FIXTURES = Path(__file__).parent / "fixtures" / "api_v1_mapping"
EXACT = Decimal("0.12345678901234567890123456789")


@pytest.fixture
def pages():
    return json.loads(
        (FIXTURES / "metagraph_pages.json").read_text(encoding="utf-8"), parse_float=Decimal
    )


@pytest.fixture
def endpoints():
    return json.loads(
        (FIXTURES / "endpoint_pages.json").read_text(encoding="utf-8"), parse_float=Decimal
    )


def envelope(rows):
    return {
        "data": rows,
        "pagination": {
            "current_page": 1,
            "per_page": max(1, len(rows)),
            "total_items": len(rows),
            "total_pages": 1 if rows else 0,
            "next_page": None,
            "prev_page": None,
        },
    }


def test_metagraph_normalization_is_exact_and_does_not_mutate(pages):
    original = deepcopy(pages)
    batch = normalize_pages(METAGRAPH, pages, netuid=7, expected_block=1000)
    assert (batch.netuid, batch.block_number, batch.loaded_at_utc) == (7, 1000, None)
    assert [record.uid for record in batch.records] == list(range(6))
    assert batch.records[1].fields["validator_trust"] == EXACT
    assert type(batch.records[1].fields["validator_trust"]) is Decimal
    assert batch.records[0].fields["validator_trust"] == Decimal(0)
    assert batch.records[4].fields["validator_trust"] is None
    assert pages == original
    pages[0]["data"][1]["incentive"] = "1"
    assert batch.records[1].fields["incentive"] == EXACT
    with pytest.raises(TypeError):
        batch.records[0].fields["incentive"] = Decimal(1)


def test_all_other_endpoints_preserve_observed_names_and_separate_times(endpoints):
    loaded = datetime(2026, 1, 2, 10, tzinfo=timezone(timedelta(hours=7)))
    for endpoint, pages in endpoints.items():
        batch = normalize_pages(endpoint, pages, netuid=7, loaded_at_utc=loaded)
        assert batch.loaded_at_utc == "2026-01-02T03:00:00+00:00"
        record = batch.records[0]
        assert record.netuid == 7
        assert record.uid is None
        if endpoint.endswith("identities"):
            assert record.source_timestamp is None
            assert record.block_number is None
            assert record.fields["subnet_name"] == "Synthetic subnet"
        else:
            assert record.source_timestamp == 123456789  # No epoch conversion.
            assert record.block_number == 1000
    metrics = normalize_pages("/v1/subnets/metrics", endpoints["/v1/subnets/metrics"], netuid=7)
    assert metrics.records[0].fields["registration_timestamp"] == 123456
    assert metrics.records[0].fields["registration_cost"] == EXACT
    pools = normalize_pages(
        "/v1/subnets/pools/aggregate", endpoints["/v1/subnets/pools/aggregate"], netuid=7
    )
    assert pools.records[0].fields["tao_volume_24_hr"] == Decimal(3)
    assert pools.records[0].fields["alpha_volume_24_hr"] == Decimal(4)
    assert pools.records[0].fields["price_change_1_hour"] == Decimal("-0.25")
    assert "volume_24h" not in pools.records[0].fields


def test_missing_optional_values_are_none_and_unobserved_fields_ignored(endpoints):
    for endpoint in endpoints:
        batch = normalize_pages(endpoint, [envelope([{"netuid": 7, "unobserved": 123}])], netuid=7)
        assert all(value is None for value in batch.records[0].fields.values())
        assert batch.records[0].source_timestamp is None
        assert "unobserved" not in batch.records[0].fields
    record = normalize_pages(
        "/v1/subnets/identities",
        [envelope([{"netuid": 7, "timestamp": "unconfirmed", "block_number": 99}])],
        netuid=7,
    ).records[0]
    assert record.source_timestamp is None
    assert record.block_number is None


@pytest.mark.parametrize("endpoint", [METAGRAPH, "/v1/subnets/metrics"])
def test_empty_complete_result(endpoint):
    batch = normalize_pages(endpoint, [envelope([])], netuid=7)
    assert batch.records == ()
    assert batch.block_number is None
    assert vtrust_avg([envelope([])], netuid=7) is None
    assert incentive_bounds(
        [envelope([])], netuid=7, filters={"is_immune": True}
    ) == IncentiveBounds(None, None, 0)


def test_vtrust_mean_includes_zero_and_excludes_inactive_and_nonpermit(pages):
    with localcontext() as context:
        context.prec = 3
        context.rounding = ROUND_UP
        context.traps[Inexact] = True
        assert vtrust_avg(pages, netuid=7, expected_block=1000) == Decimal(
            "0.061728394506172839450617283945"
        )
    for page in pages:
        for row in page["data"]:
            row["active"] = False
    assert vtrust_avg(pages, netuid=7) is None


def test_repeating_mean_has_documented_context(pages):
    rows = deepcopy(pages[0]["data"])
    for row, value in zip(rows, ["0", "0", "1"], strict=True):
        row.update(active=True, validator_permit=True, validator_trust=value)
    assert vtrust_avg([envelope(rows)], netuid=7) == Decimal("0." + "3" * 50)


def test_incentive_filters_are_explicit_and_do_not_infer_miners_or_exclude_owner(pages):
    assert incentive_bounds(pages, netuid=7, filters={"is_immune": True}) == IncentiveBounds(
        Decimal(0), Decimal("0.2"), 2
    )  # Includes UID 0 owner+permit.
    assert incentive_bounds(pages, netuid=7, filters={"is_immune": False}) == IncentiveBounds(
        Decimal(0), Decimal("0.9"), 4
    )  # Includes inactive+permit.
    assert incentive_bounds(
        pages,
        netuid=7,
        filters={
            "is_immune": True,
            "validator_permit": False,
            "in_danger": True,
        },
    ) == IncentiveBounds(Decimal("0.2"), Decimal("0.2"), 1)
    assert incentive_bounds(
        pages,
        netuid=7,
        filters={
            "is_immune": True,
            "active": False,
        },
    ) == IncentiveBounds(None, None, 0)


@pytest.mark.parametrize(
    "filters", [None, {}, {"miner": True}, {"active": "true"}, {"is_immune": 1}, {"uid": 0}]
)
def test_invalid_or_implicit_incentive_filters(pages, filters):
    with pytest.raises(MappingError):
        incentive_bounds(pages, netuid=7, filters=filters)


@pytest.mark.parametrize("field", ["netuid", "uid", "block_number"])
@pytest.mark.parametrize("value", [None, True, -1, "7", 7.0, Decimal(7)])
def test_required_identifiers_and_block_types(pages, field, value):
    pages[0]["data"][0][field] = value
    with pytest.raises(ResponseError):
        normalize_pages(METAGRAPH, pages, netuid=7)


@pytest.mark.parametrize("field", ["netuid", "uid", "block_number"])
def test_required_fields_cannot_be_omitted(pages, field):
    for page in pages:
        for row in page["data"]:
            del row[field]
    with pytest.raises(ResponseError):
        normalize_pages(METAGRAPH, pages, netuid=7)


@pytest.mark.parametrize("netuid", [True, -1, "7", 7.0, None, 8])
def test_expected_netuid_is_explicit_and_matches_every_record(pages, netuid):
    with pytest.raises(MappingError):
        normalize_pages(METAGRAPH, pages, netuid=netuid)


def test_other_subnet_cannot_hide_on_later_page(pages):
    pages[1]["data"][1]["netuid"] = 8
    with pytest.raises(MappingError, match="netuid differs"):
        vtrust_avg(pages, netuid=7)


@pytest.mark.parametrize("across_pages", [True, False])
def test_duplicate_uid_rejected(pages, across_pages):
    page = pages[1] if across_pages else pages[0]
    page["data"][1]["uid"] = 0
    with pytest.raises(ResponseError, match="duplicate"):
        vtrust_avg(pages, netuid=7)


def test_mismatched_or_unexpected_blocks_rejected(pages):
    with pytest.raises(MappingError, match="expected_block"):
        normalize_pages(METAGRAPH, pages, netuid=7, expected_block=1001)
    pages[1]["data"][0]["block_number"] = 1001
    with pytest.raises(ResponseError, match="block_number"):
        incentive_bounds(pages, netuid=7, filters={"active": True})


@pytest.mark.parametrize(
    "rows",
    [
        [{"netuid": 7, "block_number": 10}, {"netuid": 7, "block_number": 11}],
        [{"netuid": 7, "block_number": 10}, {"netuid": 7}],
    ],
)
def test_nonmetagraph_repeated_subnet_rows_are_ambiguous(rows):
    with pytest.raises(ResponseError, match="duplicate"):
        normalize_pages("/v1/subnets/pools", [envelope(rows)], netuid=7)


@pytest.mark.parametrize("indices", [[0], [1], [1, 0], [0, 0], [0, 1, 1]])
def test_incomplete_repeated_or_reordered_pages_never_produce_metrics(pages, indices):
    partial = [pages[index] for index in indices]
    with pytest.raises(ResponseError):
        vtrust_avg(partial, netuid=7)
    with pytest.raises(ResponseError):
        incentive_bounds(partial, netuid=7, filters={"active": True})


def test_different_pagination_totals_rejected(pages):
    pages[1]["pagination"]["total_items"] = 5
    pages[1]["data"].pop()
    with pytest.raises(ResponseError, match="totals changed"):
        vtrust_avg(pages, netuid=7)


@pytest.mark.parametrize(
    "field", ["active", "validator_permit", "is_immune", "is_owner_hotkey", "in_danger"]
)
@pytest.mark.parametrize("value", ["false", 0, 1, [], {}])
def test_flags_must_be_actual_booleans(pages, field, value):
    pages[0]["data"][0][field] = value
    with pytest.raises(MappingError, match="boolean"):
        normalize_pages(METAGRAPH, pages, netuid=7)


@pytest.mark.parametrize("field", ["validator_trust", "incentive"])
@pytest.mark.parametrize(
    "value",
    [
        True,
        0.1,
        "NaN",
        "Infinity",
        Decimal("NaN"),
        Decimal("Infinity"),
        "-0.1",
        "1e999",
        "0e999",
        "1e-999",
        "0." + "1" * 127,
        [],
        {},
    ],
)
def test_invalid_metric_values(pages, field, value):
    pages[0]["data"][0][field] = value
    with pytest.raises(MappingError):
        normalize_pages(METAGRAPH, pages, netuid=7)


@pytest.mark.parametrize(
    "value", [Decimal("0.12345678901234567890123456789"), "0.12345678901234567890123456789", 0]
)
def test_exact_numeric_input_representations(pages, value):
    pages[0]["data"][0]["incentive"] = value
    assert normalize_pages(METAGRAPH, pages, netuid=7).records[0].fields["incentive"] == (
        Decimal(0) if value == 0 else EXACT
    )


@pytest.mark.parametrize("field", ["active", "validator_permit", "validator_trust"])
@pytest.mark.parametrize("absent", [True, False])
def test_missing_required_vtrust_selection_or_metric_is_not_skipped(pages, field, absent):
    if absent:
        del pages[0]["data"][0][field]
    else:
        pages[0]["data"][0][field] = None
    with pytest.raises(MappingError, match="missing"):
        vtrust_avg(pages, netuid=7)


def test_all_filter_fields_checked_even_when_first_filter_does_not_match(pages):
    del pages[1]["data"][0]["active"]  # Not permit, still missing classification input.
    with pytest.raises(MappingError, match="missing"):
        vtrust_avg(pages, netuid=7)


def test_missing_selected_incentive_fails_without_reducing_count(pages):
    del pages[0]["data"][0]["incentive"]
    with pytest.raises(MappingError, match="missing"):
        incentive_bounds(pages, netuid=7, filters={"is_immune": True})


@pytest.mark.parametrize("pages", [[], None, {}, iter(())])
def test_no_implicit_iterators_or_unverified_empty_input(pages):
    with pytest.raises(MappingError):
        normalize_pages(METAGRAPH, pages, netuid=7)


@pytest.mark.parametrize("loaded", [datetime(2026, 1, 1), "2026-01-01T00:00:00Z", 123, True])
def test_loaded_time_requires_explicit_timezone(loaded):
    with pytest.raises(MappingError):
        normalize_pages(METAGRAPH, [envelope([])], netuid=7, loaded_at_utc=loaded)


@pytest.mark.parametrize("value", [True, {}, [], 1.0])
def test_unconfirmed_timestamp_representation_is_not_guessed(endpoints, value):
    pages = endpoints["/v1/subnets/metrics"]
    pages[0]["data"][0]["timestamp"] = value
    with pytest.raises(MappingError):
        normalize_pages("/v1/subnets/metrics", pages, netuid=7)


def test_expected_block_requires_evidence(endpoints):
    pages = endpoints["/v1/subnets/metrics"]
    del pages[0]["data"][0]["block_number"]
    with pytest.raises(MappingError, match="expected_block"):
        normalize_pages("/v1/subnets/metrics", pages, netuid=7, expected_block=1000)


@pytest.mark.parametrize("endpoint", ["/v1/subnets/unknown", METAGRAPH + "?netuid=7", None, []])
def test_only_documented_endpoints_are_mapped(endpoint):
    with pytest.raises(MappingError, match="endpoint"):
        normalize_pages(endpoint, [envelope([])], netuid=7)


@pytest.mark.parametrize("value", [True, -1, "1000", 1000.0])
def test_expected_block_must_be_nonnegative_integer(pages, value):
    with pytest.raises(MappingError):
        normalize_pages(METAGRAPH, pages, netuid=7, expected_block=value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("subnet_name", 123),
        ("discord", []),
        ("github_repo", True),
    ],
)
def test_identity_text_is_not_implicitly_coerced(endpoints, field, value):
    pages = endpoints["/v1/subnets/identities"]
    pages[0]["data"][0][field] = value
    with pytest.raises(MappingError, match="text"):
        normalize_pages("/v1/subnets/identities", pages, netuid=7)


@pytest.mark.parametrize("value", [-1, True, "2", 2.0, Decimal(2)])
def test_immune_count_is_a_nonnegative_integer(endpoints, value):
    pages = endpoints["/v1/subnets/metagraph/aggregate"]
    pages[0]["data"][0]["immune_count"] = value
    with pytest.raises(MappingError):
        normalize_pages("/v1/subnets/metagraph/aggregate", pages, netuid=7)


def test_source_decimal_and_zero_block_are_preserved_without_time_inference(endpoints):
    pages = endpoints["/v1/subnets/metrics"]
    pages[0]["data"][0].update(timestamp=EXACT, block_number=0, registration_timestamp=None)
    batch = normalize_pages("/v1/subnets/metrics", pages, netuid=7, expected_block=0)
    assert batch.block_number == 0
    assert batch.records[0].source_timestamp is EXACT
    assert batch.records[0].fields["registration_timestamp"] is None
    assert batch.loaded_at_utc is None


def test_mean_sum_keeps_small_terms_and_input_precision(pages):
    rows = deepcopy(pages[0]["data"][:2])
    rows[0].update(validator_trust="1", active=True, validator_permit=True)
    rows[1].update(validator_trust="1e-100", active=True, validator_permit=True)
    expected = Decimal("0.5" + "0" * 99 + "5")
    assert vtrust_avg([envelope(rows)], netuid=7) == expected


def completed(pages, endpoint=METAGRAPH, keys=None):
    target = PageMerger(endpoint, Budget(len(pages), len(pages)), key_fields=keys)
    for page in pages:
        target.reserve_next_page()
        target.accept(page)
    return target.finish_query()


def test_completed_query_equals_envelope_path_without_pagination_replay(pages, monkeypatch):
    query = completed(pages)
    batch = normalize_pages(METAGRAPH, pages, netuid=7, expected_block=1000)
    average = vtrust_avg(pages, netuid=7)
    bounds = incentive_bounds(pages, netuid=7, filters={"is_immune": True})

    def forbidden(*args, **kwargs):
        pytest.fail("CompletedQuery must not replay pagination or reserve Budget")

    monkeypatch.setattr(mapping, "Budget", forbidden)
    monkeypatch.setattr(mapping, "PageMerger", forbidden)
    monkeypatch.setattr(validation, "parse_envelope", forbidden)
    assert normalize_pages(METAGRAPH, query, netuid=7, expected_block=1000) == batch
    assert vtrust_avg(query, netuid=7) == average
    assert incentive_bounds(query, netuid=7, filters={"is_immune": True}) == bounds


def test_completed_query_checks_endpoint_netuid_and_expected_block(pages):
    query = completed(pages)
    with pytest.raises(MappingError, match="endpoint differs"):
        normalize_pages("/v1/subnets/metrics", query, netuid=7)
    with pytest.raises(MappingError, match="netuid differs"):
        normalize_pages(METAGRAPH, query, netuid=8)
    with pytest.raises(MappingError, match="expected_block"):
        vtrust_avg(query, netuid=7, expected_block=1001)
    with pytest.raises(MappingError, match="netuid differs"):
        incentive_bounds(query, netuid=8, filters={"active": True})
    with pytest.raises(ResponseError):
        normalize_pages(METAGRAPH, query.rows, netuid=7)  # Plain rows are never trusted.


@pytest.mark.parametrize(
    "field,value",
    [
        ("validator_trust", None),
        ("validator_trust", -1),
        ("validator_trust", 0.5),
        ("active", "true"),
        ("validator_permit", None),
    ],
)
def test_completed_query_does_not_bypass_metric_and_filter_validation(pages, field, value):
    pages[0]["data"][0][field] = value
    query = completed(pages)  # Pagination is valid; metric semantics may not be.
    with pytest.raises(MappingError):
        vtrust_avg(query, netuid=7)


def test_completed_query_does_not_bypass_incentive_validation(pages):
    del pages[0]["data"][0]["incentive"]
    query = completed(pages)
    with pytest.raises(MappingError, match="missing"):
        incentive_bounds(query, netuid=7, filters={"is_immune": True})


def test_completed_metagraph_still_requires_block_evidence(pages):
    for page in pages:
        for row in page["data"]:
            del row["block_number"]
    query = completed(pages)
    with pytest.raises(MappingError, match="block_number is required"):
        normalize_pages(METAGRAPH, query, netuid=7)


def test_completed_query_does_not_bypass_narrow_nonmetagraph_contract():
    pages = [envelope([{"netuid": 7, "pool_index": 0}, {"netuid": 7, "pool_index": 1}])]
    query = completed(pages, "/v1/subnets/pools", ("netuid", "pool_index"))
    with pytest.raises(MappingError, match="multiple records"):
        normalize_pages("/v1/subnets/pools", query, netuid=7)


def test_completed_empty_query_mapping_and_metrics():
    query = completed([envelope([])])
    assert normalize_pages(METAGRAPH, query, netuid=7).records == ()
    assert vtrust_avg(query, netuid=7) is None
    assert incentive_bounds(query, netuid=7, filters={"active": True}) == IncentiveBounds(
        None, None, 0
    )
