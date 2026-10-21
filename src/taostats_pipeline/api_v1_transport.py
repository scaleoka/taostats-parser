"""Opt-in, bounded Taostats HTTP. No I/O or credential reads at import time.

This module deliberately has no bulk collection or retry API. Request counts are
not credit costs. See docs/taostats-api-transport.md for the local safety boundary.
"""

import json
import os
import time
from dataclasses import dataclass, field
from decimal import Decimal
from threading import Lock
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .api_v1 import Budget, PageMerger

_BASE = "https://api.taostats.io/v1/"
_ENDPOINTS = frozenset(
    "/v1/subnets/" + suffix
    for suffix in (
        "identities",
        "metrics",
        "pools",
        "pools/aggregate",
        "metagraph/aggregate",
        "metagraph",
    )
)


class TransportError(RuntimeError):
    """Safe fixed diagnostic, without underlying HTTP exception or response."""


def _positive(value, name, maximum):
    if type(value) is not int or not 1 <= value <= maximum:
        raise TransportError(f"invalid {name}")
    return value


def _enabled(allow_live):
    if allow_live is not True:
        raise TransportError("live HTTP is disabled")


def _endpoint(endpoint):
    if not isinstance(endpoint, str) or endpoint not in _ENDPOINTS:
        raise TransportError("endpoint is not allowed")
    return endpoint


@dataclass(frozen=True)
class Query:
    """Conservative local bounds, not a claim about the server's full contract."""

    page: int = 1
    limit: int = 1
    netuid: int | None = None
    validator_permit: bool | None = None

    def __post_init__(self):
        _positive(self.page, "page", 1_000_000)
        _positive(self.limit, "limit", 200)
        if self.netuid is not None and (
            type(self.netuid) is not int or not 0 <= self.netuid <= 65535
        ):
            raise TransportError("invalid netuid")
        if self.validator_permit is not None and type(self.validator_permit) is not bool:
            raise TransportError("invalid validator_permit")

    def parameters(self, endpoint, page=None):
        if self.validator_permit is not None and endpoint != "/v1/subnets/metagraph":
            raise TransportError("validator_permit is allowed only for metagraph")
        params = {"page": self.page if page is None else page, "limit": self.limit}
        if self.netuid is not None:
            params["netuid"] = self.netuid
        if self.validator_permit is not None:
            params["validator_permit"] = str(self.validator_permit).lower()
        return params


@dataclass
class LiveAllowance:
    """Explicit small-operation allowance, independent of Budget; never credits.

    More than two calls is refused until a future confirmed credit policy exists.
    Share this object across transports to share the operation cap.
    """

    max_requests: int
    requests: int = field(default=0, init=False)
    _lock: object = field(default_factory=Lock, init=False, repr=False)

    def __post_init__(self):
        _positive(self.max_requests, "live operation allowance (maximum two)", 2)

    def reserve(self):
        with self._lock:
            if self.requests >= self.max_requests:
                raise TransportError("live operation allowance exhausted")
            self.requests += 1


class LocalRateLimiter:
    """Serializes calls at least 15 seconds apart within this instance only."""

    def __init__(self):
        self._lock = Lock()
        self._next = 0.0

    def run(self, operation):
        with self._lock:
            while (delay := self._next - time.monotonic()) > 0:
                time.sleep(delay)
            self._next = time.monotonic() + 15.0
            return operation()


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("nonfinite JSON number")


def _float(value):
    # Bound input and explicit exponent BEFORE Decimal construction. Decimal does
    # not round string input to the active context's precision.
    if len(value) > 128:
        raise ValueError("JSON number literal too long")
    _, marker, exponent = value.lower().partition("e")
    if marker and not -308 <= int(exponent) <= 308:
        raise ValueError("JSON number exponent out of range")
    number = Decimal(value)
    if not number.is_finite() or (number and not -308 <= number.adjusted() <= 308):
        raise ValueError("JSON number magnitude out of range")
    return number


class Transport:
    """Fixed-origin GET transport. Both construction and each call require opt-in.

    The private opener seam is for offline tests only. No credential is retained
    on this object. Environment proxies are disabled; redirects are never followed.
    """

    def __init__(
        self,
        budget: Budget,
        allowance: LiveAllowance,
        *,
        allow_live=False,
        limiter: LocalRateLimiter | None = None,
        timeout=20,
        max_response_bytes=1_048_576,
    ):
        _enabled(allow_live)
        if not isinstance(budget, Budget) or not isinstance(allowance, LiveAllowance):
            raise TransportError("Budget and LiveAllowance are required")
        _positive(timeout, "timeout", 60)
        _positive(max_response_bytes, "response size limit", 1_048_576)
        self._budget = budget
        self._allowance = allowance
        self._limiter = limiter if limiter is not None else LocalRateLimiter()
        self._timeout = timeout
        self._max_bytes = max_response_bytes
        self._opener = build_opener(ProxyHandler({}), _NoRedirects())
        self._lock = Lock()

    def get(self, endpoint, query=None, *, allow_live=False):
        """One decoded JSON object; envelope validation is a separate concern."""
        _enabled(allow_live)
        endpoint, query = self._validate(endpoint, query)
        with self._lock:
            self._allowance.reserve()
            self._budget.reserve_request()
            return self._exchange(endpoint, query.parameters(endpoint))

    def pages(self, endpoint, query=None, *, key_fields=None):
        """Create a page session without reading credentials or performing I/O."""
        return PageSession(self, endpoint, query, key_fields)

    @staticmethod
    def _validate(endpoint, query):
        endpoint = _endpoint(endpoint)
        if query is None:
            query = Query()
        if type(query) is not Query:
            raise TransportError("Query is required")
        query.parameters(endpoint)
        return endpoint, query

    def _exchange(self, endpoint, parameters):
        # Both entry points reserve the SAME Budget before reaching this method.
        url = _BASE + endpoint.removeprefix("/v1/") + "?" + urlencode(parameters)
        payload, error = self._limiter.run(lambda: self._wire(url))
        if error is not None:
            raise TransportError(error)
        # Raise outside the decoder's except block: no raw response in __context__.
        invalid = False
        try:
            result = json.loads(
                payload.decode("utf-8"),
                object_pairs_hook=_object,
                parse_constant=_constant,
                parse_float=_float,
            )
            invalid = type(result) is not dict
        except (ValueError, UnicodeError, RecursionError):
            invalid = True
        if invalid:
            raise TransportError("invalid JSON object response")
        return result

    def _wire(self, url):
        # Never propagate urllib exceptions: they can retain URL/headers/body.
        request = None
        response = None
        key = None
        try:
            key = os.environ.get("TAOSTATS_API_KEY")
            if not key or not key.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in key):
                return None, "TAOSTATS_API_KEY is missing or invalid"
            request = Request(
                url, headers={"Authorization": key, "Accept": "application/json"}, method="GET"
            )
            key = None
            response = self._opener.open(request, timeout=self._timeout)
            if response.geturl() != url:
                return None, "response origin or URL changed"
            if type(response.status) is not int or response.status != 200:
                return None, "HTTP request rejected"
            payload = response.read(self._max_bytes + 1)
            if len(payload) > self._max_bytes:
                return None, "HTTP response too large"
            return payload, None
        except HTTPError as error:
            try:
                error.close()
            except Exception:
                pass
            return None, "HTTP request rejected"
        except Exception:
            return None, "HTTP request failed"
        finally:
            key = None
            if request is not None:
                request.remove_header("Authorization")
            if response is not None:
                # Cleanup failures must not leak an underlying HTTP exception.
                try:
                    response.close()
                except Exception:
                    pass


class PageSession:
    """Fixed query and owned merger; failed fetches permanently close the session."""

    def __init__(self, transport, endpoint, query=None, key_fields=None):
        endpoint, query = Transport._validate(endpoint, query)
        if query.page != 1:
            raise TransportError("page sessions must start at page one")
        self._transport = transport
        self._endpoint = endpoint
        self._query = query
        self._merger = PageMerger(endpoint, transport._budget, key_fields=key_fields)
        self._failed = False

    def fetch_next_page(self, *, allow_live=False):
        _enabled(allow_live)
        if self._failed:
            raise TransportError("page session failed; discard this query")
        with self._transport._lock:
            try:
                self._transport._allowance.reserve()
                page = self._merger.reserve_next_page()
                response = self._transport._exchange(
                    self._endpoint, self._query.parameters(self._endpoint, page)
                )
                self._merger.accept(response)
            except Exception:
                self._failed = True
                raise

    def finish(self):
        if self._failed:
            raise TransportError("page session failed; discard this query")
        return self._merger.finish()
