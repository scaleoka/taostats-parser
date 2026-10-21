"""Synthetic HTTP only; the suite also blocks sockets and subprocesses."""

import importlib
import io
import json
import traceback
from decimal import Decimal, localcontext
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from taostats_pipeline import api_v1_transport as http
from taostats_pipeline.api_v1 import Budget, BudgetError, ResponseError

ENDPOINT = "/v1/subnets/metagraph"
KEY = "synthetic-test-key"
URL = "https://api.taostats.io/v1/subnets/metagraph?page=1&limit=1"


class FakeResponse(io.BytesIO):
    def __init__(self, body=b"{}", status=200, url=None):
        super().__init__(body)
        self.status = status
        self.url = url
        self.read_sizes = []

    def geturl(self):
        return self.url

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


class FakeHTTP:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def open(self, request, *, timeout):
        self.calls.append((request.full_url, dict(request.header_items()), timeout))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        response.url = response.url or request.full_url
        return response


@pytest.fixture
def clock(monkeypatch):
    now = [100.0]
    waits = []

    def sleep(delay):
        waits.append(delay)
        now[0] += delay

    monkeypatch.setattr(http.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(http.time, "sleep", sleep)
    return now, waits


@pytest.fixture
def make(monkeypatch, clock):
    monkeypatch.setenv("TAOSTATS_API_KEY", KEY)

    def create(responses=(), budget=None, allowance=None, **kwargs):
        transport = http.Transport(
            budget if budget is not None else Budget(2, 2),
            allowance if allowance is not None else http.LiveAllowance(2),
            allow_live=True,
            **kwargs,
        )
        fake = FakeHTTP(responses)
        transport._opener = fake
        return transport, fake

    return create


def test_import_configuration_and_disabled_calls_do_not_read_key_or_open(monkeypatch):
    original_get = http.os.environ.get

    def guarded_get(name, default=None):
        if name == "TAOSTATS_API_KEY":
            pytest.fail("credential read without live request")
        return original_get(name, default)

    monkeypatch.setattr(http.os.environ, "get", guarded_get)
    importlib.reload(http)
    query = http.Query()
    budget = Budget(2, 2)
    allowance = http.LiveAllowance(2)
    with pytest.raises(http.TransportError, match="disabled"):
        http.Transport(budget, allowance)
    transport = http.Transport(budget, allowance, allow_live=True)
    fake = FakeHTTP([])
    transport._opener = fake
    pages = transport.pages(ENDPOINT, query)
    for disabled in (False, None, 1, "true"):
        with pytest.raises(http.TransportError, match="disabled"):
            transport.get(ENDPOINT, allow_live=disabled)
        with pytest.raises(http.TransportError, match="disabled"):
            pages.fetch_next_page(allow_live=disabled)
    assert not fake.calls
    assert (budget.requests, budget.pages, allowance.requests) == (0, 0, 0)


def test_authorization_fixed_origin_get_and_no_retained_key(make):
    transport, fake = make([FakeResponse()])
    assert transport.get(ENDPOINT, allow_live=True) == {}
    url, headers, timeout = fake.calls[0]
    assert url == URL
    assert headers["Authorization"] == KEY  # Direct key, without Bearer.
    assert timeout == 20
    assert KEY not in repr(transport)
    assert KEY not in repr(vars(transport))


@pytest.mark.parametrize("key", [None, "", "bad\r\nheader", "ключ", "space key"])
def test_missing_or_invalid_key(make, monkeypatch, key):
    transport, fake = make()
    if key is None:
        monkeypatch.delenv("TAOSTATS_API_KEY", raising=False)
    else:
        monkeypatch.setenv("TAOSTATS_API_KEY", key)
    with pytest.raises(http.TransportError, match="missing or invalid"):
        transport.get(ENDPOINT, allow_live=True)
    assert not fake.calls


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://evil.invalid/v1/subnets/metagraph",
        "http://api.taostats.io/v1/subnets/metagraph",
        "https://api.taostats.io/v1/subnets/metagraph",
        "//evil.invalid/v1/subnets/metagraph",
        ENDPOINT + "?netuid=7",
        ENDPOINT + "#x",
        ENDPOINT + "/",
        ENDPOINT + "\n",
        "/v1/subnets/../metagraph",
        "/v1/subnets/%6detagraph",
        "/v1/subnets/unknown",
        None,
    ],
)
def test_url_domain_and_path_substitution_rejected_before_reservation(make, endpoint):
    budget = Budget(2, 2)
    transport, fake = make(budget=budget)
    with pytest.raises(http.TransportError, match="endpoint"):
        transport.get(endpoint, allow_live=True)
    assert not fake.calls
    assert budget.requests == 0


@pytest.mark.parametrize(
    "suffix",
    [
        "identities",
        "metrics",
        "pools",
        "pools/aggregate",
        "metagraph/aggregate",
        "metagraph",
    ],
)
def test_exact_six_endpoint_allowlist(make, suffix):
    transport, fake = make([FakeResponse()])
    transport.get("/v1/subnets/" + suffix, allow_live=True)
    assert fake.calls[0][0] == "https://api.taostats.io/v1/subnets/" + suffix + "?page=1&limit=1"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"page": True},
        {"page": 0},
        {"page": "1&url=evil"},
        {"page": 1_000_001},
        {"limit": False},
        {"limit": 0},
        {"limit": 201},
        {"limit": 1.0},
        {"netuid": True},
        {"netuid": -1},
        {"netuid": 65536},
        {"netuid": "7"},
        {"validator_permit": "true"},
        {"validator_permit": 1},
    ],
)
def test_query_strict_types_and_bounds(kwargs):
    with pytest.raises(http.TransportError):
        http.Query(**kwargs)


def test_query_encoding_and_endpoint_specific_filter(make):
    transport, fake = make([FakeResponse()])
    query = http.Query(limit=200, netuid=80, validator_permit=True)
    transport.get(ENDPOINT, query, allow_live=True)
    assert fake.calls[0][0] == URL.replace("limit=1", "limit=200&netuid=80&validator_permit=true")
    with pytest.raises(http.TransportError, match="only for metagraph"):
        transport.get("/v1/subnets/metrics", query, allow_live=True)
    with pytest.raises(http.TransportError, match="Query"):
        transport.get(ENDPOINT, {"page": 1}, allow_live=True)


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308, 401, 429, 500])
@pytest.mark.parametrize("raised", [False, True])
def test_http_errors_are_safe_and_never_retried(make, code, raised):
    response = FakeResponse(KEY.encode(), status=code)
    if raised:
        response = HTTPError(URL + "&secret=" + KEY, code, KEY, {"Authorization": KEY}, response)
    transport, fake = make([response])
    with pytest.raises(http.TransportError, match="HTTP request rejected") as caught:
        transport.get(ENDPOINT, allow_live=True)
    assert len(fake.calls) == 1
    assert transport._budget.requests == 1
    assert_safe(caught.value)


def assert_safe(error):
    assert error.__context__ is None
    assert error.__cause__ is None
    formatted = "".join(traceback.format_exception(error))
    for forbidden in (KEY, "https://", "Authorization", "secret="):
        assert forbidden not in str(error)
        assert forbidden not in repr(error)
        assert forbidden not in repr(vars(error))
        assert forbidden not in formatted


@pytest.mark.parametrize("error", [TimeoutError(KEY), OSError(KEY), ValueError(KEY)])
def test_io_errors_are_safe(make, error):
    transport, fake = make([error])
    with pytest.raises(http.TransportError, match="HTTP request failed") as caught:
        transport.get(ENDPOINT, allow_live=True)
    assert len(fake.calls) == 1
    assert_safe(caught.value)


@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        b"[]",
        b"null",
        b"true",
        b'"text"',
        b"1",
        b"\xff",
        b'{"x": NaN}',
        b'{"x": Infinity}',
        b'{"x": 1e999}',
        b'{"x":1,"x":2}',
        ('{"secret":"' + KEY + '"').encode(),
        b"[" * 2000,
    ],
)
def test_invalid_json_and_top_level_type(make, body):
    transport, fake = make([FakeResponse(body)])
    with pytest.raises(http.TransportError, match="invalid JSON") as caught:
        transport.get(ENDPOINT, allow_live=True)
    assert len(fake.calls) == 1
    assert_safe(caught.value)


def test_response_size_is_bounded_and_response_closed(make):
    response = FakeResponse(b" " * 33)
    transport, _ = make([response], max_response_bytes=32)
    with pytest.raises(http.TransportError, match="too large"):
        transport.get(ENDPOINT, allow_live=True)
    assert response.read_sizes == [33]
    assert response.closed
    response = FakeResponse(b"{}")
    transport, _ = make([response], max_response_bytes=2)
    assert transport.get(ENDPOINT, allow_live=True) == {}
    assert response.closed


@pytest.mark.parametrize("url", ["https://evil.invalid/", "http://api.taostats.io/v1/"])
def test_changed_response_url_is_rejected_without_reading(make, url):
    response = FakeResponse(url=url)
    transport, fake = make([response])
    with pytest.raises(http.TransportError, match="URL changed"):
        transport.get(ENDPOINT, allow_live=True)
    assert not response.read_sizes
    assert len(fake.calls) == 1


def test_real_opener_disables_redirects_and_environment_proxies():
    transport = http.Transport(Budget(1, 1), http.LiveAllowance(1), allow_live=True)
    handlers = transport._opener.handlers
    redirects = [handler for handler in handlers if isinstance(handler, http.HTTPRedirectHandler)]
    assert len(redirects) == 1
    request = Request(URL)
    for code in (301, 302, 303, 307, 308):
        assert (
            redirects[0].redirect_request(request, None, code, "", {}, "https://evil.invalid")
            is None
        )
    # urllib drops empty ProxyHandler from handlers, installing no proxy handler.
    assert not any(isinstance(handler, http.ProxyHandler) for handler in handlers)


@pytest.mark.parametrize("limits", [(0, 2), (1, 2)])
def test_request_budget_before_connection_and_after_exhaustion(make, limits):
    budget = Budget(*limits)
    transport, fake = make([FakeResponse()], budget=budget)
    if limits[0]:
        transport.get(ENDPOINT, allow_live=True)
    with pytest.raises(BudgetError):
        transport.get(ENDPOINT, allow_live=True)
    assert len(fake.calls) == limits[0]
    assert budget.requests == limits[0]


def test_shared_budget_and_allowance(make):
    budget = Budget(2, 2)
    allowance = http.LiveAllowance(1)
    first, _ = make([FakeResponse()], budget, allowance)
    second, fake = make(budget=budget, allowance=allowance)
    first.get(ENDPOINT, allow_live=True)
    with pytest.raises(http.TransportError, match="allowance exhausted"):
        second.get(ENDPOINT, allow_live=True)
    assert not fake.calls
    assert budget.requests == 1


@pytest.mark.parametrize("value", [0, 3, True, "2", -1])
def test_mass_operation_allowance_rejected(value):
    with pytest.raises(http.TransportError):
        http.LiveAllowance(value)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"timeout": 0},
        {"timeout": 61},
        {"timeout": float("inf")},
        {"max_response_bytes": 0},
        {"max_response_bytes": 1_048_577},
    ],
)
def test_finite_bounded_configuration(kwargs):
    with pytest.raises(http.TransportError):
        http.Transport(Budget(1, 1), http.LiveAllowance(1), allow_live=True, **kwargs)


def test_limiter_is_shared_across_transports_and_waits_fifteen_seconds(make, clock):
    limiter = http.LocalRateLimiter()
    first, _ = make([FakeResponse()], limiter=limiter)
    second, _ = make([FakeResponse()], limiter=limiter)
    first.get(ENDPOINT, allow_live=True)
    second.get(ENDPOINT, allow_live=True)
    assert clock[1] == [15.0]


def page(number):
    return FakeResponse(
        json.dumps(
            {
                "data": [{"netuid": 80, "uid": number, "block_number": 100}],
                "pagination": {
                    "current_page": number,
                    "per_page": 1,
                    "total_items": 2,
                    "total_pages": 2,
                    "next_page": 2 if number == 1 else None,
                    "prev_page": 1 if number == 2 else None,
                },
            }
        ).encode()
    )


def test_two_pages_reuse_merger_without_double_reservation(make):
    budget = Budget(2, 2)
    transport, fake = make([page(1), page(2)], budget=budget)
    session = transport.pages(ENDPOINT, http.Query(netuid=80))
    session.fetch_next_page(allow_live=True)
    session.fetch_next_page(allow_live=True)
    assert [row["uid"] for row in session.finish()] == [1, 2]
    assert (budget.requests, budget.pages) == (2, 2)
    assert len(fake.calls) == 2
    assert "page=2&limit=1&netuid=80" in fake.calls[1][0]
    with pytest.raises(http.TransportError):
        session.fetch_next_page(allow_live=True)
    assert len(fake.calls) == 2


@pytest.mark.parametrize("limits", [(0, 2), (2, 0), (1, 2), (2, 1)])
def test_page_budget_rejected_before_connection(make, limits):
    budget = Budget(*limits)
    transport, fake = make([page(1)], budget=budget)
    session = transport.pages(ENDPOINT)
    expected_calls = min(*limits, 1)
    if expected_calls:
        session.fetch_next_page(allow_live=True)
    with pytest.raises(BudgetError):
        session.fetch_next_page(allow_live=True)
    assert len(fake.calls) == expected_calls
    assert (budget.requests, budget.pages) == (expected_calls, expected_calls)


@pytest.mark.parametrize("response", [FakeResponse(b"{}"), TimeoutError(KEY)])
def test_failed_page_session_cannot_return_partial_data_or_retry(make, response):
    transport, fake = make([page(1), response])
    session = transport.pages(ENDPOINT)
    session.fetch_next_page(allow_live=True)
    with pytest.raises((http.TransportError, ResponseError)):
        session.fetch_next_page(allow_live=True)
    with pytest.raises(http.TransportError, match="failed"):
        session.finish()
    with pytest.raises(http.TransportError, match="failed"):
        session.fetch_next_page(allow_live=True)
    assert len(fake.calls) == 2
    assert (transport._budget.requests, transport._budget.pages) == (2, 2)


def test_urllib_redirect_chain_never_opens_second_connection(make):
    from email.message import Message
    from urllib.request import HTTPSHandler, build_opener
    from urllib.response import addinfourl

    requests = []

    class SyntheticHTTPS(HTTPSHandler):
        def https_open(self, request):
            requests.append(request)
            headers = Message()
            headers["Location"] = "https://evil.invalid/?secret=" + KEY
            response = addinfourl(io.BytesIO(KEY.encode()), headers, request.full_url, 302)
            response.msg = "Found"
            return response

    transport, _ = make()
    transport._opener = build_opener(http.ProxyHandler({}), http._NoRedirects(), SyntheticHTTPS())
    with pytest.raises(http.TransportError, match="HTTP request rejected") as caught:
        transport.get(ENDPOINT, allow_live=True)
    assert_safe(caught.value)
    assert len(requests) == 1
    assert not requests[0].has_header("Authorization")


def test_page_session_constructor_cannot_bypass_endpoint_or_query_checks(make):
    transport, fake = make()
    for create in (transport.pages, lambda *args: http.PageSession(transport, *args)):
        with pytest.raises(http.TransportError, match="endpoint"):
            create("/v1/subnets/unknown", http.Query())
        with pytest.raises(http.TransportError, match="page one"):
            create(ENDPOINT, http.Query(page=2))
        with pytest.raises(http.TransportError, match="Query"):
            create(ENDPOINT, {"page": 1})
    assert not fake.calls
    assert transport._budget.requests == 0


def test_shared_budget_between_get_and_page_session(make):
    budget = Budget(1, 2)
    first, _ = make([FakeResponse()], budget=budget)
    second, fake = make(budget=budget)
    first.get(ENDPOINT, allow_live=True)
    session = second.pages(ENDPOINT)
    with pytest.raises(BudgetError):
        session.fetch_next_page(allow_live=True)
    assert (budget.requests, budget.pages) == (1, 0)
    assert not fake.calls


def test_budget_denial_precedes_key_read_and_limiter(make, monkeypatch):
    transport, fake = make(budget=Budget(0, 0))
    original_get = http.os.environ.get

    def guarded_get(name, default=None):
        if name == "TAOSTATS_API_KEY":
            pytest.fail("credential read after budget denial")
        return original_get(name, default)

    def forbidden_wait(operation):
        pytest.fail("limiter reached after budget denial")

    monkeypatch.setattr(http.os.environ, "get", guarded_get)
    monkeypatch.setattr(transport._limiter, "run", forbidden_wait)
    with pytest.raises(BudgetError):
        transport.get(ENDPOINT, allow_live=True)
    assert not fake.calls


def test_fractional_json_preserves_decimal_precision(make):
    literal = "0.12345678901234567890123456789"
    transport, _ = make([FakeResponse(('{"x":' + literal + "}").encode())])
    with localcontext() as context:
        context.prec = 6
        result = transport.get(ENDPOINT, allow_live=True)
    assert type(result["x"]) is Decimal
    assert result["x"] == Decimal(literal)
    assert str(result["x"]) == literal


@pytest.mark.parametrize(
    "literal",
    [
        "1e999",
        "1e309",
        "1e-309",
        "1e" + "9" * 100,
        "0e999",
        "0.1e-308",
        "99e308",
        "0." + "1" * 127,
    ],
)
def test_excessive_fractional_json_numbers_are_rejected(make, literal):
    transport, _ = make([FakeResponse(('{"x":' + literal + "}").encode())])
    with pytest.raises(http.TransportError, match="invalid JSON") as caught:
        transport.get(ENDPOINT, allow_live=True)
    assert_safe(caught.value)


@pytest.mark.parametrize("literal", ["1e308", "1e-308", "0." + "1" * 126])
def test_fractional_json_numeric_bounds_are_inclusive(make, literal):
    transport, _ = make([FakeResponse(('{"x":' + literal + "}").encode())])
    assert transport.get(ENDPOINT, allow_live=True)["x"] == Decimal(literal)


@pytest.mark.parametrize("literal", ["1e999", "1e-999", "0e999", "0." + "1" * 127])
def test_excessive_numbers_rejected_before_decimal_construction(monkeypatch, literal):
    def forbidden_decimal(value):
        pytest.fail("Decimal constructed before numeric limits were checked")

    monkeypatch.setattr(http, "Decimal", forbidden_decimal)
    with pytest.raises(ValueError):
        http._float(literal)
