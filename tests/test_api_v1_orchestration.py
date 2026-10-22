"""Synthetic orchestration tests; shared conftest blocks sockets and subprocesses."""

import io
import json
import os
from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from email.message import Message
from types import SimpleNamespace
from urllib.request import HTTPSHandler
from urllib.response import addinfourl

import pytest

from taostats_pipeline import api_v1 as api
from taostats_pipeline import api_v1_mapping as mapping
from taostats_pipeline import api_v1_orchestration as orchestration
from taostats_pipeline import api_v1_transport as http
from taostats_pipeline.api_v1 import Budget, BudgetError, PageMerger, ResponseError
from taostats_pipeline.api_v1_orchestration import (
    MetagraphAnalysis,
    analyze_metagraph,
    complete_metagraph,
    fetch_metagraph,
)

ENDPOINT = "/v1/subnets/metagraph"
EXACT = Decimal("0.12345678901234567890123456789")
MEAN = Decimal("0.061728394506172839450617283945")


def row(uid=0, *, netuid=7, block=1000, trust=EXACT, active=True, permit=True):
    return {
        "netuid": netuid,
        "uid": uid,
        "block_number": block,
        "active": active,
        "validator_permit": permit,
        "validator_trust": trust,
        "incentive": EXACT,
        "is_immune": uid % 2 == 0,
    }


def page(rows, number=1, size=None, total=None):
    size = max(len(rows), 1) if size is None else size
    total = len(rows) if total is None else total
    pages = (total + size - 1) // size
    return {
        "data": rows,
        "pagination": {
            "current_page": number,
            "per_page": size,
            "total_items": total,
            "total_pages": pages,
            "next_page": number + 1 if number < pages else None,
            "prev_page": number - 1 if number > 1 else None,
        },
    }


def wire(envelope):
    # The precision-critical value is an actual JSON number through urllib.
    return json.dumps(envelope, default=str).replace('"' + str(EXACT) + '"', str(EXACT)).encode()


@pytest.fixture
def pages():
    rows = [row(0, trust=0), row(1), row(2, trust=1, active=False), row(3, permit=False)]
    return [page(rows[:2], 1, 2, 4), page(rows[2:], 2, 2, 4)]


@pytest.fixture
def rig(monkeypatch):
    responses, calls, key_reads, waits, streams = [], [], [], [], []
    now = [100.0]
    original_get = os.environ.get

    def environment_get(name, default=None):
        if name == "TAOSTATS_API_KEY":
            key_reads.append(now[0])
            return "synthetic-orchestration-key"
        return original_get(name, default)

    def sleep(seconds):
        waits.append(seconds)
        now[0] += seconds

    def https_open(handler, request):
        calls.append((request.full_url, now[0]))
        assert request.get_header("Authorization") == "synthetic-orchestration-key"
        body, status = responses.pop(0)
        stream = io.BytesIO(body)
        streams.append(stream)
        response = addinfourl(stream, Message(), request.full_url, status)
        response.msg = "OK" if status == 200 else "Synthetic failure"
        return response

    monkeypatch.setattr(os.environ, "get", environment_get)
    monkeypatch.setattr(http.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(http.time, "sleep", sleep)
    monkeypatch.setattr(HTTPSHandler, "https_open", https_open)

    def transport(budget=None, allowance=None, limiter=None):
        budget = budget if budget is not None else Budget(2, 2)
        allowance = allowance if allowance is not None else http.LiveAllowance(2)
        return (
            http.Transport(budget, allowance, limiter=limiter, allow_live=True),
            budget,
            allowance,
        )

    return SimpleNamespace(
        responses=responses,
        calls=calls,
        key_reads=key_reads,
        waits=waits,
        streams=streams,
        transport=transport,
    )


@pytest.mark.parametrize("two_pages", [False, True])
def test_offline_complete_and_analyze_preserves_decimal_and_budget(pages, two_pages):
    envelopes = pages if two_pages else [page([r for p in pages for r in p["data"]])]
    before = deepcopy(envelopes)
    budget = Budget(2, 2)
    query = complete_metagraph(envelopes, budget=budget)
    assert query.endpoint == ENDPOINT
    assert query.total_items == 4
    with localcontext() as context:
        context.prec = 3
        result = analyze_metagraph(
            query,
            netuid=7,
            expected_block=1000,
            loaded_at_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
            incentive_filters={"is_immune": True},
        )
    assert isinstance(result, MetagraphAnalysis)
    assert result.vtrust_computed is True
    assert result.vtrust_avg == MEAN
    assert type(result.vtrust_avg) is Decimal
    assert str(result.batch.records[1].fields["validator_trust"]) == str(EXACT)
    assert result.batch.loaded_at_utc == "2026-01-01T00:00:00+00:00"
    assert [r.uid for r in result.batch.records] == [0, 1, 2, 3]
    assert result.incentive_bounds == mapping.IncentiveBounds(EXACT, EXACT, 2)
    assert (budget.requests, budget.pages) == (len(envelopes), len(envelopes))
    assert envelopes == before
    envelopes[0]["data"][0]["netuid"] = 8
    assert result.batch.netuid == 7
    assert not hasattr(result, "query")
    with pytest.raises(FrozenInstanceError):
        result.vtrust_computed = False
    with pytest.raises(TypeError):
        result.batch.records[0].fields["active"] = False


def test_empty_and_uncomputed_metrics_are_distinct():
    query = complete_metagraph([page([])], budget=Budget(1, 1))
    result = analyze_metagraph(query, netuid=7, incentive_filters={"active": True})
    assert result.batch.records == ()
    assert result.batch.block_number is None
    assert result.vtrust_computed is True and result.vtrust_avg is None
    assert result.incentive_bounds == mapping.IncentiveBounds(None, None, 0)
    skipped = analyze_metagraph(query, netuid=7, compute_vtrust=False)
    assert skipped.vtrust_computed is False and skipped.vtrust_avg is None
    assert skipped.incentive_bounds is None


def test_offline_does_not_read_credentials_wait_or_enter_http(rig):
    query = complete_metagraph((page([row()]),), budget=Budget(1, 1))
    assert analyze_metagraph(query, netuid=7).vtrust_avg == EXACT
    assert rig.calls == rig.key_reads == rig.waits == []


@pytest.mark.parametrize(
    "envelopes",
    [[], (), None, {}, iter(()), [page([])] * 3, [None], [{"data": {}}], [page([None])]],
)
def test_invalid_offline_materialization_or_shape(envelopes):
    budget = Budget(2, 2)
    with pytest.raises(ResponseError):
        complete_metagraph(envelopes, budget=budget)
    assert (budget.requests, budget.pages) == (0, 0)


@pytest.mark.parametrize("count,two_pages", [(401, False), (402, False), (401, True)])
def test_offline_limit_preflight_precedes_any_deepcopy(monkeypatch, count, two_pages):
    def forbidden(*args, **kwargs):
        pytest.fail("oversized input was copied")

    monkeypatch.setattr(api, "deepcopy", forbidden)
    rows = [row(i) for i in range(count)]
    envelopes = (
        [page(rows[:200], 1, 200, count), page(rows[200:], 2, 200, count)]
        if two_pages
        else [page(rows)]
    )
    budget = Budget(2, 2)
    with pytest.raises(ResponseError, match="400 actual"):
        complete_metagraph(envelopes, budget=budget)
    assert (budget.requests, budget.pages) == (0, 0)


@pytest.mark.parametrize("two_pages", [False, True])
def test_offline_400_record_limit_is_inclusive(two_pages):
    rows = [row(i) for i in range(400)]
    envelopes = (
        [page(rows[:200], 1, 200, 400), page(rows[200:], 2, 200, 400)]
        if two_pages
        else [page(rows)]
    )
    query = complete_metagraph(envelopes, budget=Budget(2, 2))
    assert query.total_items == 400
    assert len(analyze_metagraph(query, netuid=7).batch.records) == 400


@pytest.mark.parametrize("value", [None, True, -1, "7", 7.0, 65536])
def test_offline_invalid_netuid_rejected_before_budget(value):
    budget = Budget(1, 1)
    with pytest.raises(ResponseError, match="netuid"):
        complete_metagraph([page([row(netuid=value)])], budget=budget)
    assert budget.requests == budget.pages == 0


def test_offline_mixed_subnets_rejected_before_budget(pages):
    pages[1]["data"][0]["netuid"] = 8
    budget = Budget(2, 2)
    with pytest.raises(ResponseError, match="netuid differs"):
        complete_metagraph(pages, budget=budget)
    assert budget.requests == budget.pages == 0


@pytest.mark.parametrize("failure", ["missing", "reordered", "duplicate", "block", "totals"])
def test_offline_invalid_sequence_never_returns_query(pages, failure):
    if failure == "missing":
        pages.pop()
    elif failure == "reordered":
        pages.reverse()
    elif failure == "duplicate":
        pages[1]["data"][0]["uid"] = 0
    elif failure == "block":
        pages[1]["data"][0]["block_number"] = 1001
    else:
        pages[1]["pagination"]["total_items"] = 3
        pages[1]["data"].pop()
    with pytest.raises(ResponseError):
        complete_metagraph(pages, budget=Budget(2, 2))


def test_offline_uses_shared_budget_without_replacement():
    budget = Budget(1, 1)
    complete_metagraph([page([row()])], budget=budget)
    with pytest.raises(BudgetError):
        complete_metagraph([page([row()])], budget=budget)
    assert budget.requests == budget.pages == 1


def test_live_two_pages_then_analysis_use_original_limits(rig, pages):
    rig.responses.extend((wire(p), 200) for p in pages)
    transport, budget, allowance = rig.transport()
    query = fetch_metagraph(transport, netuid=7, page_count=2, limit=2, allow_live=True)
    result = analyze_metagraph(
        query, netuid=7, expected_block=1000, incentive_filters={"is_immune": True}
    )
    assert result.vtrust_avg == MEAN
    assert str(result.batch.records[1].fields["validator_trust"]) == str(EXACT)
    assert (budget.requests, budget.pages, allowance.requests) == (2, 2, 2)
    assert rig.calls == [
        (f"https://api.taostats.io{ENDPOINT}?page={i}&limit=2&netuid=7", when)
        for i, when in ((1, 100.0), (2, 115.0))
    ]
    assert rig.key_reads == [100.0, 115.0]
    assert rig.waits == [15.0]
    assert all(stream.closed for stream in rig.streams)


@pytest.mark.parametrize("flag", [False, None, 1, "true"])
def test_live_requires_exact_true_before_transport_access(flag):
    class ForbiddenTransport:
        def pages(self, *args, **kwargs):
            pytest.fail("disabled live reached transport")

    with pytest.raises(http.TransportError, match="disabled"):
        fetch_metagraph(ForbiddenTransport(), netuid=7, page_count=1, allow_live=flag)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"page_count": 0},
        {"page_count": 3},
        {"page_count": True},
        {"page_count": "1"},
        {"limit": 0},
        {"limit": 201},
        {"limit": True},
        {"limit": 1.0},
        {"netuid": None},
        {"netuid": True},
        {"netuid": -1},
        {"netuid": 65536},
    ],
)
def test_live_invalid_plan_denied_before_reservations(rig, kwargs):
    transport, budget, allowance = rig.transport()
    plan = {"netuid": 7, "page_count": 1, "limit": 1, **kwargs}
    with pytest.raises(ResponseError):
        fetch_metagraph(transport, **plan, allow_live=True)
    assert (budget.requests, budget.pages, allowance.requests) == (0, 0, 0)
    assert rig.calls == rig.key_reads == rig.waits == []


@pytest.mark.parametrize(
    "budget,live_limit", [(Budget(1, 2), 2), (Budget(2, 1), 2), (Budget(2, 2), 1)]
)
def test_live_denial_does_not_replace_budget_or_allowance(rig, pages, budget, live_limit):
    rig.responses.extend((wire(p), 200) for p in pages)
    transport, _, allowance = rig.transport(budget, http.LiveAllowance(live_limit))
    error = http.TransportError if live_limit == 1 else BudgetError
    with pytest.raises(error):
        fetch_metagraph(transport, netuid=7, page_count=2, allow_live=True)
    assert budget.requests == budget.pages == 1
    assert allowance.requests == live_limit
    assert len(rig.calls) == len(rig.key_reads) == 1
    assert rig.waits == []
    assert len(rig.responses) == 1


@pytest.mark.parametrize("failure", ["json", "429", "500", "duplicate", "block"])
def test_live_error_never_retries_or_returns_partial_query(rig, pages, failure):
    status = 200
    if failure == "duplicate":
        pages[1]["data"][0]["uid"] = 0
    if failure == "block":
        pages[1]["data"][0]["block_number"] = 1001
    second = wire(pages[1])
    if failure == "json":
        second = b"not JSON"
    elif failure in ("429", "500"):
        status = int(failure)
    rig.responses.extend([(wire(pages[0]), 200), (second, status)])
    transport, budget, allowance = rig.transport()
    error = ResponseError if failure in ("duplicate", "block") else http.TransportError
    with pytest.raises(error):
        fetch_metagraph(transport, netuid=7, page_count=2, allow_live=True)
    assert len(rig.calls) == 2
    assert (budget.requests, budget.pages, allowance.requests) == (2, 2, 2)
    assert rig.responses == []


@pytest.mark.parametrize("page_count,actual_pages", [(1, 2), (2, 1)])
def test_live_inexact_plan_fails_without_expanding_or_probing(rig, pages, page_count, actual_pages):
    envelopes = pages if actual_pages == 2 else [page([row()])]
    rig.responses.extend((wire(p), 200) for p in envelopes)
    transport, budget, _ = rig.transport()
    with pytest.raises(ResponseError):
        fetch_metagraph(transport, netuid=7, page_count=page_count, allow_live=True)
    assert len(rig.calls) == 1
    assert budget.requests == budget.pages == 1


@pytest.mark.parametrize("page_count", [1, 2])
def test_live_actual_count_checked_even_if_server_ignores_limit(rig, monkeypatch, page_count):
    if page_count == 1:
        envelopes = [page([row(i) for i in range(401)])]
    else:
        rows = [row(i) for i in range(402)]
        envelopes = [page(rows[:201], 1, 201, 402), page(rows[201:], 2, 201, 402)]
    rig.responses.extend((wire(p), 200) for p in envelopes)
    transport, budget, allowance = rig.transport()

    def forbidden_rows(query):
        pytest.fail("oversized completed query was copied again")

    monkeypatch.setattr(api.CompletedQuery, "rows", property(forbidden_rows))
    with pytest.raises(ResponseError, match="400 actual"):
        fetch_metagraph(transport, netuid=7, page_count=page_count, limit=1, allow_live=True)
    assert (budget.requests, budget.pages, allowance.requests) == (page_count,) * 3
    assert len(rig.calls) == page_count


@pytest.mark.parametrize("actual_netuid", [8, None])
def test_live_checks_response_subnet_before_returning_query(rig, actual_netuid):
    rig.responses.append((wire(page([row(netuid=actual_netuid)])), 200))
    transport, _, _ = rig.transport()
    with pytest.raises(ResponseError):
        fetch_metagraph(transport, netuid=7, page_count=1, allow_live=True)
    assert len(rig.calls) == 1


def test_shared_limiter_is_not_replaced(rig):
    rig.responses.extend([(wire(page([row()])), 200), (wire(page([row()])), 200)])
    limiter = http.LocalRateLimiter()
    for _ in range(2):
        transport, _, _ = rig.transport(limiter=limiter)
        fetch_metagraph(transport, netuid=7, page_count=1, allow_live=True)
    assert [when for _, when in rig.calls] == [100.0, 115.0]
    assert rig.waits == [15.0]


def test_analysis_does_not_replay_pagination_or_create_limits(pages, monkeypatch):
    query = complete_metagraph(pages, budget=Budget(2, 2))

    def forbidden(*args, **kwargs):
        pytest.fail("analysis replayed pagination or constructed a limit")

    monkeypatch.setattr(api, "parse_envelope", forbidden)
    monkeypatch.setattr(mapping, "PageMerger", forbidden)
    monkeypatch.setattr(mapping, "Budget", forbidden)
    monkeypatch.setattr(orchestration, "Budget", forbidden)
    result = analyze_metagraph(query, netuid=7, incentive_filters={"is_immune": True})
    assert result.vtrust_avg == MEAN


def test_analysis_order_and_skip_unrequested_metrics(pages, monkeypatch):
    query = complete_metagraph(pages, budget=Budget(2, 2))
    calls = []

    def spy(name, implementation):
        def call(*args, **kwargs):
            calls.append(name)
            return implementation(*args, **kwargs)

        return call

    for name in ("normalize_pages", "vtrust_avg", "incentive_bounds"):
        monkeypatch.setattr(orchestration, name, spy(name, getattr(orchestration, name)))
    analyze_metagraph(query, netuid=7, incentive_filters={"is_immune": True})
    assert calls == ["normalize_pages", "vtrust_avg", "incentive_bounds"]
    calls.clear()
    skipped = analyze_metagraph(query, netuid=7, compute_vtrust=False)
    assert calls == ["normalize_pages"]
    assert skipped.vtrust_computed is False and skipped.incentive_bounds is None


@pytest.mark.parametrize("failure", ["vtrust", "incentive", "block", "netuid"])
def test_analysis_failure_does_not_construct_partial_result(pages, monkeypatch, failure):
    if failure == "vtrust":
        pages[0]["data"][0]["validator_trust"] = None
    if failure == "incentive":
        pages[0]["data"][0]["incentive"] = None
    query = complete_metagraph(pages, budget=Budget(2, 2))
    created = []
    original = MetagraphAnalysis

    def result(*args, **kwargs):
        created.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(orchestration, "MetagraphAnalysis", result)
    with pytest.raises(ResponseError):
        analyze_metagraph(
            query,
            netuid=8 if failure == "netuid" else 7,
            expected_block=1001 if failure == "block" else 1000,
            incentive_filters={"is_immune": True},
        )
    assert created == []


@pytest.mark.parametrize("flag", [None, 1, "false"])
def test_compute_vtrust_requires_boolean(flag):
    query = complete_metagraph([page([])], budget=Budget(1, 1))
    with pytest.raises(ResponseError, match="boolean"):
        analyze_metagraph(query, netuid=7, compute_vtrust=flag)


def test_analyze_requires_completed_metagraph_and_enforces_limits():
    with pytest.raises(ResponseError, match="CompletedQuery"):
        analyze_metagraph([page([])], netuid=7)
    target = PageMerger("/v1/subnets/metrics", Budget(1, 1), key_fields=("netuid",))
    target.reserve_next_page()
    target.accept(page([{"netuid": 7}]))
    with pytest.raises(ResponseError, match="endpoint"):
        analyze_metagraph(target.finish_query(), netuid=7)
    target = PageMerger(ENDPOINT, Budget(1, 1))
    target.reserve_next_page()
    target.accept(page([row(i) for i in range(401)]))
    with pytest.raises(ResponseError, match="limits"):
        analyze_metagraph(target.finish_query(), netuid=7)


@pytest.mark.parametrize("budget", [None, 1, True, {}])
def test_offline_requires_explicit_budget(budget):
    with pytest.raises(ResponseError, match="Budget"):
        complete_metagraph([page([])], budget=budget)


def test_live_is_disabled_by_default_without_key_lookup_or_reservation(rig):
    transport, budget, allowance = rig.transport()
    with pytest.raises(http.TransportError, match="disabled"):
        fetch_metagraph(transport, netuid=7, page_count=1)
    assert (budget.requests, budget.pages, allowance.requests) == (0, 0, 0)
    assert rig.calls == rig.key_reads == rig.waits == []


def test_live_allowance_is_not_reset_between_operations(rig):
    rig.responses.extend([(wire(page([row()])), 200)] * 2)
    transport, budget, allowance = rig.transport(allowance=http.LiveAllowance(1))
    first = fetch_metagraph(transport, netuid=7, page_count=1, allow_live=True)
    with pytest.raises(http.TransportError, match="allowance exhausted"):
        fetch_metagraph(transport, netuid=7, page_count=1, allow_live=True)
    assert first.total_items == 1
    assert (budget.requests, budget.pages, allowance.requests) == (1, 1, 1)
    assert len(rig.calls) == 1
    assert len(rig.responses) == 1


def test_live_400_records_with_maximum_explicit_plan(rig):
    rows = [row(i) for i in range(400)]
    envelopes = [page(rows[:200], 1, 200, 400), page(rows[200:], 2, 200, 400)]
    rig.responses.extend((wire(p), 200) for p in envelopes)
    transport, budget, allowance = rig.transport()
    query = fetch_metagraph(transport, netuid=7, page_count=2, limit=200, allow_live=True)
    assert query.total_items == 400
    assert len(analyze_metagraph(query, netuid=7).batch.records) == 400
    assert (budget.requests, budget.pages, allowance.requests) == (2, 2, 2)


def test_explicitly_skipped_vtrust_does_not_require_selection_fields():
    record = row(trust=None)
    del record["active"]
    query = complete_metagraph([page([record])], budget=Budget(1, 1))
    result = analyze_metagraph(query, netuid=7, compute_vtrust=False)
    assert result.vtrust_computed is False and result.vtrust_avg is None
    with pytest.raises(ResponseError, match="missing"):
        analyze_metagraph(query, netuid=7)


def test_netuid_zero_is_not_treated_as_missing():
    query = complete_metagraph([page([row(netuid=0)])], budget=Budget(1, 1))
    assert analyze_metagraph(query, netuid=0).batch.netuid == 0


@pytest.mark.parametrize("filters", [{}, {"active": "true"}, {"miner": True}])
def test_explicit_invalid_incentive_filters_are_not_silently_skipped(filters):
    query = complete_metagraph([page([row()])], budget=Budget(1, 1))
    with pytest.raises(ResponseError):
        analyze_metagraph(query, netuid=7, incentive_filters=filters)


def test_analysis_rejects_completed_three_page_query_before_copying(monkeypatch):
    merger = PageMerger(ENDPOINT, Budget(3, 3))
    for number in (1, 2, 3):
        merger.reserve_next_page()
        merger.accept(page([row(number)], number, 1, 3))
    query = merger.finish_query()

    def forbidden(query):
        pytest.fail("out-of-scope query was copied")

    monkeypatch.setattr(api.CompletedQuery, "rows", property(forbidden))
    with pytest.raises(ResponseError, match="limits"):
        analyze_metagraph(query, netuid=7)
