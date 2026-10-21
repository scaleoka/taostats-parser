"""Offline integration through real urllib handlers, decoding and page validation.

PageSession.finish() returns merged decoded rows, not page envelopes or a
NormalizedBatch. A test-only observer of PUBLIC PageMerger.accept records its
actual decoded inputs. Only after finish succeeds are these envelopes supplied
to the mapper. This is instrumentation, not a production adapter or pagination
implementation. No private state is read or modified. Transport.get is tested
separately as the public endpoint that already returns envelopes.
"""

import io
import json
import os
from contextlib import contextmanager
from copy import deepcopy
from decimal import Decimal, localcontext
from email.message import Message
from types import SimpleNamespace
from urllib.request import HTTPSHandler
from urllib.response import addinfourl

import pytest

from taostats_pipeline import api_v1_transport as http
from taostats_pipeline.api_v1 import Budget, BudgetError, PageMerger, ResponseError
from taostats_pipeline.api_v1_mapping import normalize_pages, vtrust_avg

ENDPOINT = "/v1/subnets/metagraph"
LITERAL = "0.12345678901234567890123456789"
EXACT = Decimal(LITERAL)
MEAN = Decimal("0.061728394506172839450617283945")


def wire_page(number, *, duplicate=False, block=1000, netuid=7):
    """Fixed synthetic two-page response; metrics are JSON numbers, not strings."""
    rows = (
        [
            {"uid": 0, "active": True, "validator_permit": True, "validator_trust": "ZERO"},
            {"uid": 1, "active": True, "validator_permit": True, "validator_trust": "EXACT"},
        ]
        if number == 1
        else [
            {
                "uid": 1 if duplicate else 2,
                "active": False,
                "validator_permit": True,
                "validator_trust": "ONE",
            },
            {"uid": 3, "active": True, "validator_permit": False, "validator_trust": "ONE"},
        ]
    )
    for row in rows:
        row.update(netuid=netuid, block_number=block, incentive="EXACT")
    envelope = {
        "data": rows,
        "pagination": {
            "current_page": number,
            "per_page": 2,
            "total_items": 4,
            "total_pages": 2,
            "next_page": 2 if number == 1 else None,
            "prev_page": 1 if number == 2 else None,
        },
    }
    body = json.dumps(envelope)
    for marker, literal in (("ZERO", "0.0"), ("EXACT", LITERAL), ("ONE", "1.0")):
        body = body.replace('"' + marker + '"', literal)
    return body.encode("utf-8")


@pytest.fixture
def rig(monkeypatch):
    """Fake only HTTP I/O and clock/environment boundaries, keeping urllib real."""
    now = [100.0]
    waits, calls, key_reads, responses, streams = [], [], [], [], []
    original_get = os.environ.get

    def environment_get(name, default=None):
        if name == "TAOSTATS_API_KEY":
            key_reads.append(now[0])
            return "synthetic-integration-key"  # Never read a real API key.
        return original_get(name, default)

    def sleep(delay):
        waits.append(delay)
        now[0] += delay

    def https_open(handler, request):
        calls.append((request.full_url, request.get_method(), now[0]))
        assert request.get_header("Authorization") == "synthetic-integration-key"
        body, status = responses.pop(0)
        stream = io.BytesIO(body)
        streams.append(stream)
        response = addinfourl(stream, Message(), request.full_url, status)
        response.msg = "OK" if status == 200 else "Synthetic HTTP failure"
        return response

    monkeypatch.setattr(os.environ, "get", environment_get)
    monkeypatch.setattr(http.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(http.time, "sleep", sleep)
    monkeypatch.setattr(HTTPSHandler, "https_open", https_open)
    return SimpleNamespace(
        responses=responses,
        calls=calls,
        key_reads=key_reads,
        waits=waits,
        streams=streams,
    )


@contextmanager
def observe_envelopes(monkeypatch):
    """Observe public accept() inputs without replacing any validation logic."""
    envelopes = []
    original_accept = PageMerger.accept

    def accept(merger, response):
        original_accept(merger, response)
        envelopes.append(deepcopy(response))

    with monkeypatch.context() as observation:
        observation.setattr(PageMerger, "accept", accept)
        yield envelopes
    # Observer is removed before mapper creates its own offline PageMerger.


def test_page_session_to_mapping_preserves_decimal_and_exact_request_accounting(rig, monkeypatch):
    rig.responses.extend([(wire_page(1), 200), (wire_page(2), 200)])
    budget, allowance = Budget(2, 2), http.LiveAllowance(2)
    transport = http.Transport(budget, allowance, allow_live=True)
    session = transport.pages(ENDPOINT, http.Query(netuid=7, limit=2))
    with observe_envelopes(monkeypatch) as envelopes:
        session.fetch_next_page(allow_live=True)
        assert (budget.requests, budget.pages, allowance.requests) == (1, 1, 1)
        session.fetch_next_page(allow_live=True)
        rows = session.finish()  # Required success gate before downstream mapping.
    assert len(envelopes) == 2
    assert rows == [row for envelope in envelopes for row in envelope["data"]]
    assert [row["uid"] for row in rows] == [0, 1, 2, 3]
    assert {row["netuid"] for row in rows} == {7}
    assert {row["block_number"] for row in rows} == {1000}
    assert type(envelopes[0]["data"][1]["validator_trust"]) is Decimal
    assert str(envelopes[0]["data"][1]["validator_trust"]) == LITERAL
    assert type(rows[0]["validator_trust"]) is Decimal
    assert rows[0]["validator_trust"] == Decimal(0)
    assert rows[1]["validator_trust"].as_tuple() == EXACT.as_tuple()
    with localcontext() as context:
        context.prec = 4
        batch = normalize_pages(ENDPOINT, envelopes, netuid=7, expected_block=1000)
        mean = vtrust_avg(envelopes, netuid=7, expected_block=1000)
    assert [record.uid for record in batch.records] == [0, 1, 2, 3]
    assert batch.block_number == 1000
    assert {record.netuid for record in batch.records} == {7}
    assert type(batch.records[1].fields["validator_trust"]) is Decimal
    assert str(batch.records[1].fields["validator_trust"]) == LITERAL
    assert all(type(record.fields["incentive"]) is Decimal for record in batch.records)
    assert type(mean) is Decimal
    assert mean == MEAN  # Includes zero, excludes inactive and non-permit rows.
    assert (budget.requests, budget.pages, allowance.requests) == (2, 2, 2)
    assert [call[0] for call in rig.calls] == [
        f"https://api.taostats.io{ENDPOINT}?page={number}&limit=2&netuid=7" for number in (1, 2)
    ]
    assert [call[1:] for call in rig.calls] == [("GET", 100.0), ("GET", 115.0)]
    assert rig.waits == [15.0]
    assert rig.key_reads == [100.0, 115.0]
    assert all(stream.closed for stream in rig.streams)
    # This interface mismatch is real: merged rows cannot be passed as envelopes.
    with pytest.raises(ResponseError):
        normalize_pages(ENDPOINT, rows, netuid=7)


def test_transport_get_already_supplies_envelopes_through_public_api(rig):
    rig.responses.extend([(wire_page(1), 200), (wire_page(2), 200)])
    budget, allowance = Budget(2, 2), http.LiveAllowance(2)
    transport = http.Transport(budget, allowance, allow_live=True)
    first = transport.get(ENDPOINT, http.Query(netuid=7, limit=2, page=1), allow_live=True)
    second = transport.get(ENDPOINT, http.Query(netuid=7, limit=2, page=2), allow_live=True)
    envelopes = [first, second]  # Explicit test inputs, not an automatic API traversal.
    batch = normalize_pages(ENDPOINT, envelopes, netuid=7, expected_block=1000)
    assert [record.uid for record in batch.records] == [0, 1, 2, 3]
    assert str(batch.records[1].fields["validator_trust"]) == LITERAL
    assert vtrust_avg(envelopes, netuid=7) == MEAN
    assert (budget.requests, budget.pages, allowance.requests) == (2, 0, 2)
    assert len(rig.calls) == 2
    assert rig.waits == [15.0]


def test_incomplete_session_and_observed_envelopes_cannot_return_partial_results(rig, monkeypatch):
    rig.responses.append((wire_page(1), 200))
    budget, allowance = Budget(2, 2), http.LiveAllowance(2)
    session = http.Transport(budget, allowance, allow_live=True).pages(ENDPOINT)
    with observe_envelopes(monkeypatch) as envelopes:
        session.fetch_next_page(allow_live=True)
        with pytest.raises(ResponseError, match="incomplete"):
            session.finish()
    with pytest.raises(ResponseError, match="incomplete"):
        normalize_pages(ENDPOINT, envelopes, netuid=7)
    with pytest.raises(ResponseError, match="incomplete"):
        vtrust_avg(envelopes, netuid=7)
    with pytest.raises(ResponseError, match="failed"):
        session.fetch_next_page(allow_live=True)
    assert len(rig.calls) == 1
    assert (budget.requests, budget.pages) == (1, 1)


@pytest.mark.parametrize(
    "failure", ["duplicate_uid", "different_block", "invalid_json", "http_429"]
)
def test_second_page_failure_poisoning_and_no_partial_mapping(rig, monkeypatch, failure):
    second = {
        "duplicate_uid": (wire_page(2, duplicate=True), 200),
        "different_block": (wire_page(2, block=1001), 200),
        "invalid_json": (b'{"data": invalid', 200),
        "http_429": (b"untrusted error response", 429),
    }[failure]
    rig.responses.extend([(wire_page(1), 200), second])
    budget, allowance = Budget(2, 2), http.LiveAllowance(2)
    session = http.Transport(budget, allowance, allow_live=True).pages(ENDPOINT)
    error = (
        ResponseError if failure in ("duplicate_uid", "different_block") else http.TransportError
    )
    with observe_envelopes(monkeypatch) as envelopes:
        session.fetch_next_page(allow_live=True)
        messages = {
            "duplicate_uid": "duplicate",
            "different_block": "block_number",
            "invalid_json": "invalid JSON",
            "http_429": "HTTP request rejected",
        }
        with pytest.raises(error, match=messages[failure]):
            session.fetch_next_page(allow_live=True)
    with pytest.raises(http.TransportError, match="failed"):
        session.finish()
    with pytest.raises(http.TransportError, match="failed"):
        session.fetch_next_page(allow_live=True)
    assert len(envelopes) == 1  # Observer records only pages accepted by the real merger.
    with pytest.raises(ResponseError, match="incomplete"):
        normalize_pages(ENDPOINT, envelopes, netuid=7)
    with pytest.raises(ResponseError, match="incomplete"):
        vtrust_avg(envelopes, netuid=7)
    assert (budget.requests, budget.pages, allowance.requests) == (2, 2, 2)
    assert len(rig.calls) == 2  # No retry and no continuation after failure.
    assert len(rig.key_reads) == 2
    assert rig.waits == [15.0]
    assert all(stream.closed for stream in rig.streams)


@pytest.mark.parametrize("limits,live_limit", [((1, 2), 2), ((2, 1), 2), ((2, 2), 1)])
def test_budget_or_allowance_denial_precedes_http_and_poison_session(rig, limits, live_limit):
    rig.responses.extend([(wire_page(1), 200), (wire_page(2), 200)])
    budget, allowance = Budget(*limits), http.LiveAllowance(live_limit)
    session = http.Transport(budget, allowance, allow_live=True).pages(ENDPOINT)
    session.fetch_next_page(allow_live=True)
    error = http.TransportError if live_limit == 1 else BudgetError
    with pytest.raises(error):
        session.fetch_next_page(allow_live=True)
    with pytest.raises(http.TransportError, match="failed"):
        session.finish()
    with pytest.raises(http.TransportError, match="failed"):
        session.fetch_next_page(allow_live=True)
    assert (budget.requests, budget.pages) == (1, 1)
    assert allowance.requests == live_limit
    assert len(rig.calls) == len(rig.key_reads) == 1
    assert rig.waits == []  # Denied before limiter, key lookup and connection.
    assert len(rig.responses) == 1


def test_disabled_live_never_reads_key_or_enters_urllib(rig):
    budget, allowance = Budget(2, 2), http.LiveAllowance(2)
    with pytest.raises(http.TransportError, match="disabled"):
        http.Transport(budget, allowance)
    transport = http.Transport(budget, allowance, allow_live=True)
    session = transport.pages(ENDPOINT)
    with pytest.raises(http.TransportError, match="disabled"):
        transport.get(ENDPOINT)
    with pytest.raises(http.TransportError, match="disabled"):
        session.fetch_next_page()
    assert (budget.requests, budget.pages, allowance.requests) == (0, 0, 0)
    assert rig.calls == rig.key_reads == rig.waits == []


def test_mapper_rejects_other_netuid_even_after_successful_session(rig, monkeypatch):
    rig.responses.extend([(wire_page(1), 200), (wire_page(2, netuid=8), 200)])
    budget, allowance = Budget(2, 2), http.LiveAllowance(2)
    session = http.Transport(budget, allowance, allow_live=True).pages(
        ENDPOINT, http.Query(netuid=7, limit=2)
    )
    with observe_envelopes(monkeypatch) as envelopes:
        session.fetch_next_page(allow_live=True)
        session.fetch_next_page(allow_live=True)
        rows = session.finish()
    assert {row["netuid"] for row in rows} == {7, 8}
    with pytest.raises(ResponseError, match="netuid differs"):
        normalize_pages(ENDPOINT, envelopes, netuid=7)
    with pytest.raises(ResponseError, match="netuid differs"):
        vtrust_avg(envelopes, netuid=7)
    assert (budget.requests, budget.pages, allowance.requests) == (2, 2, 2)
    assert len(rig.calls) == 2
