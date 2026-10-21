"""Offline response validation, without transport or conversion to HTML models.

Pass decoded JSON objects to PageMerger: reserve_next_page(), then accept(response),
then finish() or finish_query() after the last page. Reservations count planned requests, including
failed responses; they are not refunded and do not estimate unknown credit costs.
Any validation failure poisons the merger; partial results cannot be returned.
Non-metagraph callers must explicitly choose integer key fields for their query
(e.g. ("netuid",) only when one row per subnet is expected).
"""

import re
from copy import deepcopy
from dataclasses import dataclass, field


class ResponseError(ValueError):
    """Invalid or incomplete offline response sequence."""


class BudgetError(ResponseError):
    """A planned operation would exceed the local budget."""


def _integer(value: object, name: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ResponseError(f"{name} must be an integer >= {minimum}")
    return value


@dataclass
class Budget:
    """Limits planned requests/pages, not credits or requests per minute.

    Shared budgets can bound multiple mergers. reserve_request() accounts for
    other planned requests without a page. No I/O, retries or waiting occur here.
    """

    max_requests: int
    max_pages: int
    requests: int = field(default=0, init=False)
    pages: int = field(default=0, init=False)

    def __post_init__(self):
        _integer(self.max_requests, "max_requests")
        _integer(self.max_pages, "max_pages")

    def reserve_request(self) -> None:
        if self.requests >= self.max_requests:
            raise BudgetError("request budget exhausted")
        self.requests += 1

    def reserve_page(self) -> None:
        if self.pages >= self.max_pages:
            raise BudgetError("page budget exhausted")
        if self.requests >= self.max_requests:
            raise BudgetError("request budget exhausted")
        self.requests += 1
        self.pages += 1


@dataclass(frozen=True)
class Pagination:
    current_page: int
    per_page: int
    total_items: int
    total_pages: int
    next_page: int | None
    prev_page: int | None


def parse_envelope(response: object) -> tuple[list[dict], Pagination]:
    """Validate decoded JSON. Unknown fields are preserved, never transformed.

    Local contract: contiguous full pages except the last; empty results accept
    total_pages=0 or 1, current_page=1, and null links. This is a conservative
    offline contract, not proof that every live endpoint follows these rules.
    """
    if not isinstance(response, dict):
        raise ResponseError("envelope must be an object")
    data, raw = response.get("data"), response.get("pagination")
    if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
        raise ResponseError("data must be an array of objects")
    if not isinstance(raw, dict):
        raise ResponseError("pagination must be an object")
    names = ("current_page", "per_page", "total_items", "total_pages", "next_page", "prev_page")
    if any(name not in raw for name in names):
        raise ResponseError("missing pagination field")
    current = _integer(raw["current_page"], "current_page", 1)
    size = _integer(raw["per_page"], "per_page", 1)
    total = _integer(raw["total_items"], "total_items")
    pages = _integer(raw["total_pages"], "total_pages")
    for name in ("next_page", "prev_page"):
        if raw[name] is not None:
            _integer(raw[name], name, 1)
    expected_pages = (total + size - 1) // size
    if pages != expected_pages and not (total == 0 and pages == 1):
        raise ResponseError("total_pages inconsistent with total_items/per_page")
    if current > max(pages, 1):
        raise ResponseError("current_page exceeds total_pages")
    expected_next = current + 1 if current < pages else None
    expected_prev = current - 1 if current > 1 else None
    if raw["next_page"] != expected_next or raw["prev_page"] != expected_prev:
        raise ResponseError("invalid next_page/prev_page")
    expected_count = min(size, max(0, total - (current - 1) * size))
    if len(data) != expected_count:
        raise ResponseError("page record count inconsistent with total_items")
    return deepcopy(data), Pagination(current, size, total, pages, expected_next, expected_prev)


@dataclass(frozen=True, init=False, repr=False, eq=False, slots=True)
class CompletedQuery:
    """Isolated, pagination-validated rows, created by PageMerger.finish_query().

    Not a normalized batch or a security boundary against private-state tampering.
    Public metadata is immutable; rows returns a fresh deep copy on every access.
    """

    endpoint: str
    total_items: int
    total_pages: int
    _rows: list[dict]

    def __new__(cls, *args, **kwargs):
        raise TypeError("use PageMerger.finish_query() to obtain a CompletedQuery")

    @property
    def rows(self) -> list[dict]:
        return deepcopy(self._rows)


class PageMerger:
    """One query/filter set. No result until complete; no atomicity claim.

    endpoint must be a canonical /v1/subnets/... path with lowercase ASCII
    segments (letters, digits, underscores, hyphens), without trailing slash,
    query, fragment, scheme, host, percent encoding or dot segments.
    Metagraph keys are (netuid, uid). If block_number appears in a query,
    all its rows must contain the same nonnegative integer block, across pages.
    Other endpoints' blocks/timestamps are retained without atomicity assertions.
    """

    def __init__(self, endpoint: str, budget: Budget, *, key_fields: tuple[str, ...] | None = None):
        if (
            not isinstance(endpoint, str)
            or re.fullmatch(r"/v1/subnets/[a-z][a-z0-9_-]*(?:/[a-z][a-z0-9_-]*)*", endpoint) is None
        ):
            raise ResponseError("endpoint must be a canonical /v1/subnets/... path only")
        self._metagraph = endpoint == "/v1/subnets/metagraph"
        if self._metagraph:
            if key_fields not in (None, ("netuid", "uid")):
                raise ResponseError("metagraph requires (netuid, uid) keys")
            key_fields = ("netuid", "uid")
        if not isinstance(key_fields, tuple) or not key_fields:
            raise ResponseError("explicit key_fields required for non-metagraph queries")
        if any(not isinstance(key, str) or not key for key in key_fields):
            raise ResponseError("key_fields must contain nonempty field names")
        if len(set(key_fields)) != len(key_fields):
            raise ResponseError("duplicate key field")
        self._endpoint = endpoint
        self._keys = key_fields
        self._budget = budget
        self._rows: list[dict] = []
        self._seen: set[tuple[int, ...]] = set()
        self._block: int | None = None
        self._block_seen = False
        self._first: Pagination | None = None
        self._next: int | None = 1
        self._pending = False
        self._failed = False

    def _guard(self):
        if self._failed:
            raise ResponseError("merger failed; discard this query")

    def reserve_next_page(self) -> int:
        """Must succeed BEFORE obtaining the next response; consumes both limits."""
        self._guard()
        try:
            if self._pending or self._next is None:
                raise ResponseError("page already reserved or query complete")
            self._budget.reserve_page()
            self._pending = True
            return self._next
        except ResponseError:
            self._failed = True
            raise

    def accept(self, response: object) -> None:
        self._guard()
        try:
            if not self._pending:
                raise ResponseError("reserve a page before accepting its response")
            rows, page = parse_envelope(response)
            if page.current_page != self._next:
                raise ResponseError("missing, repeated or reordered page")
            if self._first is not None:
                first = self._first
                if (page.per_page, page.total_items, page.total_pages) != (
                    first.per_page,
                    first.total_items,
                    first.total_pages,
                ):
                    raise ResponseError("pagination totals changed across pages")
            for row in rows:
                key = tuple(_integer(row.get(name), name) for name in self._keys)
                if key in self._seen:
                    raise ResponseError("duplicate record key")
                self._seen.add(key)
                if self._metagraph:
                    block = row.get("block_number")
                    if "block_number" in row:
                        block = _integer(block, "block_number")
                    if self._block_seen and self._block != block:
                        raise ResponseError("metagraph block_number differs or is missing")
                    self._block = block
                    self._block_seen = True
            self._rows.extend(rows)
            self._first = self._first or page
            self._next = page.next_page
            self._pending = False
        except ResponseError:
            self._failed = True
            raise

    def _require_complete(self) -> None:
        self._guard()
        if self._first is None or self._pending or self._next is not None:
            self._failed = True
            raise ResponseError("incomplete pagination")
        if len(self._rows) != self._first.total_items:
            self._failed = True
            raise ResponseError("merged count differs from total_items")

    def finish(self) -> list[dict]:
        self._require_complete()
        return deepcopy(self._rows)

    def finish_query(self) -> CompletedQuery:
        """Export a detached snapshot; no reservation, I/O or pagination replay.

        Repeatable while healthy. Any later invalid accept/reservation still
        poisons this merger as before, but cannot change an exported snapshot.
        """
        self._require_complete()
        # No public constructor/factory accepts arbitrary rows as validated data.
        result = object.__new__(CompletedQuery)
        object.__setattr__(result, "endpoint", self._endpoint)
        object.__setattr__(result, "total_items", self._first.total_items)
        object.__setattr__(result, "total_pages", self._first.total_pages)
        object.__setattr__(result, "_rows", deepcopy(self._rows))
        return result
