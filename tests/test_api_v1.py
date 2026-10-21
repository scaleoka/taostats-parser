"""Synthetic offline API responses; conftest blocks network and subprocesses."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from taostats_pipeline.api_v1 import (
    Budget,
    BudgetError,
    CompletedQuery,
    PageMerger,
    ResponseError,
    parse_envelope,
)

FIXTURES = Path(__file__).parent / "fixtures" / "api_v1"
METAGRAPH = "/v1/subnets/metagraph"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def envelope(rows, current=1, size=None, total=None):
    size = size if size is not None else max(len(rows), 1)
    total = total if total is not None else len(rows)
    pages = (total + size - 1) // size
    return {
        "data": rows,
        "pagination": {
            "current_page": current,
            "per_page": size,
            "total_items": total,
            "total_pages": pages,
            "next_page": current + 1 if current < pages else None,
            "prev_page": current - 1 if current > 1 else None,
        },
    }


def merger(budget=None):
    return PageMerger(METAGRAPH, budget if budget is not None else Budget(10, 10))


def accept_all(responses, target=None):
    target = target if target is not None else merger()
    for number, response in enumerate(responses, 1):
        assert target.reserve_next_page() == number
        target.accept(response)
    return target.finish()


def test_single_page_and_preservation():
    source = fixture("single.json")
    before = deepcopy(source)
    target = PageMerger("/v1/subnets/metrics", Budget(1, 1), key_fields=("netuid",))
    result = accept_all([source], target)
    assert result == before["data"]
    assert source == before
    source["data"][0]["extra"]["label"] = "changed"
    result[0]["extra"]["label"] = "also changed"
    assert target.finish() == before["data"]


def test_multiple_pages():
    pages = fixture("metagraph_pages.json")
    budget = Budget(2, 2)
    assert accept_all(pages, merger(budget)) == [row for page in pages for row in page["data"]]
    assert (budget.requests, budget.pages) == (2, 2)


@pytest.mark.parametrize("total_pages", [0, 1])
def test_empty_result(total_pages):
    response = fixture("empty.json")
    response["pagination"]["total_pages"] = total_pages
    assert accept_all([response]) == []


@pytest.mark.parametrize("indices", [[0], [1, 0], [0, 0], [0, 2]])
def test_incomplete_repeated_reordered_or_missing_pages(indices):
    pages = [envelope([{"netuid": 7, "uid": i}], i + 1, 1, 3) for i in range(3)]
    with pytest.raises(ResponseError):
        accept_all([pages[i] for i in indices])


def test_extra_page_and_accept_without_reservation():
    target = merger()
    with pytest.raises(ResponseError, match="reserve"):
        target.accept(envelope([]))
    with pytest.raises(ResponseError, match="failed"):
        target.finish()
    target = merger()
    accept_all([envelope([])], target)
    with pytest.raises(ResponseError, match="complete"):
        target.reserve_next_page()
    with pytest.raises(ResponseError):
        target.finish()


@pytest.mark.parametrize(
    "response",
    [
        None,
        [],
        "{}",
        {},
        {"data": [], "pagination": []},
        {"data": {}, "pagination": {}},
        {"data": [1], "pagination": {}},
    ],
)
def test_invalid_envelope(response):
    with pytest.raises(ResponseError):
        parse_envelope(response)


@pytest.mark.parametrize(
    "name", ["current_page", "per_page", "total_items", "total_pages", "next_page", "prev_page"]
)
@pytest.mark.parametrize("value", [True, False, "1", 1.0, -1, [], {}])
def test_pagination_strict_types(name, value):
    response = envelope([])
    response["pagination"][name] = value
    with pytest.raises(ResponseError):
        parse_envelope(response)


@pytest.mark.parametrize(
    "name", ["current_page", "per_page", "total_items", "total_pages", "next_page", "prev_page"]
)
def test_pagination_required_fields(name):
    response = envelope([])
    del response["pagination"][name]
    with pytest.raises(ResponseError):
        parse_envelope(response)


@pytest.mark.parametrize(
    "name,value",
    [
        ("current_page", 0),
        ("per_page", 0),
        ("current_page", 2),
        ("total_items", 3),
        ("total_pages", 2),
        ("next_page", 2),
        ("prev_page", 1),
        ("total_items", None),
    ],
)
def test_inconsistent_pagination(name, value):
    response = envelope([{"netuid": 7, "uid": 0}])
    response["pagination"][name] = value
    with pytest.raises(ResponseError):
        parse_envelope(response)


def test_wrong_page_length_and_early_null_next():
    response = envelope([{"netuid": 7, "uid": 0}], size=2, total=3)
    with pytest.raises(ResponseError, match="count"):
        parse_envelope(response)
    response = envelope([{"netuid": 7, "uid": 0}], size=1, total=2)
    response["pagination"]["next_page"] = None
    with pytest.raises(ResponseError, match="next_page"):
        parse_envelope(response)


def test_totals_change_between_valid_pages():
    target = merger()
    target.reserve_next_page()
    target.accept(envelope([{"netuid": 7, "uid": 0}], size=1, total=2))
    target.reserve_next_page()
    with pytest.raises(ResponseError, match="totals changed"):
        target.accept(envelope([{"netuid": 7, "uid": 1}], current=2, size=1, total=3))
    with pytest.raises(ResponseError, match="failed"):
        target.finish()


@pytest.mark.parametrize("across_pages", [False, True])
@pytest.mark.parametrize(
    "endpoint,keys", [(METAGRAPH, None), ("/v1/subnets/identities", ("netuid",))]
)
def test_duplicate_keys(endpoint, keys, across_pages):
    row = {"netuid": 7, "uid": 0}
    pages = [envelope([row, row])]
    if across_pages:
        pages = [envelope([row], current=i, size=1, total=2) for i in (1, 2)]
    with pytest.raises(ResponseError, match="duplicate"):
        accept_all(pages, PageMerger(endpoint, Budget(2, 2), key_fields=keys))


def test_uid_is_scoped_to_netuid_and_no_implicit_subnet_uniqueness():
    rows = [{"netuid": 7, "uid": 0}, {"netuid": 42, "uid": 0}]
    assert accept_all([envelope(rows)]) == rows
    with pytest.raises(ResponseError, match="explicit"):
        PageMerger("/v1/subnets/pools", Budget(1, 1))
    rows = [{"netuid": 7, "pool_index": 0}, {"netuid": 7, "pool_index": 1}]
    target = PageMerger("/v1/subnets/pools", Budget(1, 1), key_fields=("netuid", "pool_index"))
    assert accept_all([envelope(rows)], target) == rows
    with pytest.raises(ResponseError, match="metagraph requires"):
        PageMerger(METAGRAPH, Budget(1, 1), key_fields=("netuid",))


@pytest.mark.parametrize("name", ["netuid", "uid"])
@pytest.mark.parametrize("value", [True, False, None, "7", 7.0, -1])
def test_invalid_record_keys(name, value):
    row = {"netuid": 7, "uid": 0, name: value}
    with pytest.raises(ResponseError):
        accept_all([envelope([row])])


@pytest.mark.parametrize("limits", [(0, 1), (1, 0)])
def test_no_initial_budget(limits):
    target = merger(Budget(*limits))
    with pytest.raises(BudgetError):
        target.reserve_next_page()
    with pytest.raises(ResponseError):
        target.finish()


@pytest.mark.parametrize("limits", [(1, 2), (2, 1)])
def test_budget_exhausted_before_obtaining_next_page(limits):
    pages = fixture("metagraph_pages.json")
    iterator = iter(pages)
    budget = Budget(*limits)
    target = merger(budget)
    assert target.reserve_next_page() == 1
    target.accept(next(iterator))
    with pytest.raises(BudgetError):
        target.reserve_next_page()
    # Page two has never been obtained; denial consumes neither counter.
    assert next(iterator) == pages[1]
    assert (budget.requests, budget.pages) == (1, 1)
    with pytest.raises(ResponseError):
        target.finish()


def test_request_only_reservation_and_shared_budget():
    budget = Budget(2, 5)
    budget.reserve_request()
    accept_all([envelope([])], merger(budget))
    assert (budget.requests, budget.pages) == (2, 1)
    with pytest.raises(BudgetError):
        merger(budget).reserve_next_page()
    with pytest.raises(BudgetError):
        budget.reserve_request()


@pytest.mark.parametrize("value", [True, -1, "2", 1.0, None])
def test_invalid_budget_limits(value):
    with pytest.raises(ResponseError):
        Budget(value, 1)
    with pytest.raises(ResponseError):
        Budget(1, value)


@pytest.mark.parametrize("blocks", [(100, 101), (100, None), (None, 100)])
@pytest.mark.parametrize("across_pages", [False, True])
def test_metagraph_inconsistent_blocks(blocks, across_pages):
    rows = [{"netuid": 7, "uid": i} for i in range(2)]
    for row, block in zip(rows, blocks, strict=True):
        if block is not None:
            row["block_number"] = block
    pages = [envelope(rows)]
    if across_pages:
        pages = [envelope([row], current=i + 1, size=1, total=2) for i, row in enumerate(rows)]
    with pytest.raises(ResponseError, match="block_number"):
        accept_all(pages)


@pytest.mark.parametrize("value", [True, None, "100", 100.0, -1])
def test_invalid_metagraph_block(value):
    with pytest.raises(ResponseError):
        accept_all([envelope([{"netuid": 7, "uid": 0, "block_number": value}])])


def test_other_endpoints_do_not_assert_atomicity():
    rows = [{"netuid": 7, "block_number": 100}, {"netuid": 42, "block_number": 101}]
    target = PageMerger("/v1/subnets/metrics", Budget(1, 1), key_fields=("netuid",))
    assert accept_all([envelope(rows)], target) == rows


def test_pending_page_cannot_be_reserved_twice_or_finished():
    target = merger()
    target.reserve_next_page()
    with pytest.raises(ResponseError):
        target.reserve_next_page()
    with pytest.raises(ResponseError):
        target.finish()
    target = merger()
    target.reserve_next_page()
    with pytest.raises(ResponseError, match="incomplete"):
        target.finish()


@pytest.mark.parametrize(
    "endpoint",
    [
        METAGRAPH + "?netuid=7",
        METAGRAPH + "?",
        METAGRAPH + "#section",
        METAGRAPH + "#",
        "https://example.invalid" + METAGRAPH,
        "//example.invalid" + METAGRAPH,
        "v1/subnets/metagraph",
        METAGRAPH + "/",
        "/v1/subnets/./metagraph",
        "/v1/subnets/pools/../metagraph",
        "/v1/subnets/%6detagraph",
        "/v1/subnets//metagraph",
        METAGRAPH + "\n",
        METAGRAPH + " ",
        "",
        None,
        True,
    ],
)
def test_endpoint_rejected_before_budget_reservation(endpoint):
    budget = Budget(0, 0)
    # Explicit alternative keys must not bypass metagraph-specific checks.
    with pytest.raises(ResponseError, match="endpoint must"):
        PageMerger(endpoint, budget, key_fields=("netuid",))
    assert (budget.requests, budget.pages) == (0, 0)


@pytest.mark.parametrize(
    "endpoint", [METAGRAPH, "/v1/subnets/metrics", "/v1/subnets/metagraph/aggregate"]
)
def test_normal_endpoint_path(endpoint):
    budget = Budget(1, 1)
    keys = None if endpoint == METAGRAPH else ("netuid",)
    target = PageMerger(endpoint, budget, key_fields=keys)
    assert (budget.requests, budget.pages) == (0, 0)
    assert accept_all([envelope([])], target) == []


@pytest.mark.parametrize("args", [(), (METAGRAPH, []), ([],)])
def test_completed_query_has_no_public_data_constructor(args):
    with pytest.raises(TypeError, match="finish_query"):
        CompletedQuery(*args)


@pytest.mark.parametrize("order", [("finish", "finish_query"), ("finish_query", "finish")])
def test_both_finish_methods_repeat_without_budget_use_and_isolate_nested_data(order):
    budget = Budget(1, 1)
    target = merger(budget)
    source = envelope([{"netuid": 7, "uid": 0, "extra": {"items": [{"value": "original"}]}}])
    target.reserve_next_page()
    target.accept(source)
    for name in order * 2:
        result = getattr(target, name)()
        rows = result.rows if isinstance(result, CompletedQuery) else result
        assert rows[0]["extra"]["items"][0]["value"] == "original"
        rows[0]["extra"]["items"][0]["value"] = "external mutation"
        rows.append({"netuid": 999})
    query = target.finish_query()
    source["data"][0]["extra"]["items"].clear()
    assert query.rows == target.finish()
    assert query.rows[0]["extra"]["items"][0]["value"] == "original"
    assert (query.endpoint, query.total_items, query.total_pages) == (METAGRAPH, 1, 1)
    for name, value in (
        ("endpoint", "/v1/subnets/metrics"),
        ("total_items", 999),
        ("total_pages", 999),
        ("rows", []),
    ):
        with pytest.raises((AttributeError, TypeError)):
            setattr(query, name, value)
    assert (budget.requests, budget.pages) == (1, 1)


@pytest.mark.parametrize("total_pages", [0, 1])
def test_completed_empty_query(total_pages):
    response = envelope([])
    response["pagination"]["total_pages"] = total_pages
    target = merger(Budget(1, 1))
    target.reserve_next_page()
    target.accept(response)
    query = target.finish_query()
    assert query.rows == target.finish() == []
    assert (query.total_items, query.total_pages) == (0, total_pages)


@pytest.mark.parametrize("state", ["no_pages", "pending", "incomplete", "invalid"])
def test_completed_query_never_exported_from_unfinished_or_failed_merger(state):
    target = merger()
    if state != "no_pages":
        target.reserve_next_page()
    if state == "incomplete":
        target.accept(envelope([{"netuid": 7, "uid": 0}], size=1, total=2))
    if state == "invalid":
        with pytest.raises(ResponseError):
            target.accept({})
    with pytest.raises(ResponseError):
        target.finish_query()
    with pytest.raises(ResponseError, match="failed"):
        target.finish()
    with pytest.raises(ResponseError, match="failed"):
        target.finish_query()


@pytest.mark.parametrize("operation", ["reserve_next_page", "accept"])
def test_no_new_pages_after_completion_and_exported_query_remains_detached(operation):
    budget = Budget(2, 2)
    target = merger(budget)
    target.reserve_next_page()
    target.accept(envelope([{"netuid": 7, "uid": 0}]))
    query = target.finish_query()
    with pytest.raises(ResponseError):
        if operation == "accept":
            target.accept(envelope([{"netuid": 7, "uid": 1}]))
        else:
            target.reserve_next_page()
    for finish in (target.finish, target.finish_query):
        with pytest.raises(ResponseError, match="failed"):
            finish()
    assert query.rows == [{"netuid": 7, "uid": 0}]
    assert (budget.requests, budget.pages) == (1, 1)
