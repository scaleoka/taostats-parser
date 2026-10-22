"""Bounded, opt-in orchestration for one metagraph; no publication or retries."""

from dataclasses import dataclass
from decimal import Decimal

from . import api_v1_transport as http
from .api_v1 import Budget, CompletedQuery, PageMerger, ResponseError
from .api_v1_mapping import (
    IncentiveBounds,
    NormalizedBatch,
    incentive_bounds,
    normalize_pages,
    vtrust_avg,
)

_ENDPOINT = "/v1/subnets/metagraph"
_MAX_PAGES = 2
_MAX_ROWS = 400


@dataclass(frozen=True)
class MetagraphAnalysis:
    """Final analysis only: None plus vtrust_computed distinguishes no calculation.

    incentive_bounds=None means not requested; an empty computed group has
    IncentiveBounds(None, None, 0). No raw query or HTTP state is retained.
    """

    batch: NormalizedBatch
    vtrust_avg: Decimal | None
    vtrust_computed: bool
    incentive_bounds: IncentiveBounds | None


def _netuid(value):
    if type(value) is not int or not 0 <= value <= 65535:
        raise ResponseError("netuid must be an integer from 0 to 65535")
    return value


def _single_subnet(rows, expected=None):
    for row in rows:
        if type(row) is not dict:
            raise ResponseError("metagraph records must be objects")
        actual = _netuid(row.get("netuid"))
        if expected is None:
            expected = actual
        elif actual != expected:
            raise ResponseError("metagraph netuid differs or multiple subnets were supplied")
    return expected


def complete_metagraph(envelopes, *, budget: Budget) -> CompletedQuery:
    """Validate explicit offline pages, capped at two pages and 400 actual rows.

    Preflight resource and subnet checks precede merger's defensive deep copies.
    All pagination/UID/block checks remain in PageMerger. No HTTP or credentials.
    """
    if type(envelopes) not in (list, tuple) or not 1 <= len(envelopes) <= _MAX_PAGES:
        raise ResponseError("one or two materialized metagraph page envelopes required")
    if not isinstance(budget, Budget):
        raise ResponseError("Budget is required")
    count = 0
    subnet = None
    for envelope in envelopes:
        if type(envelope) is not dict or type(envelope.get("data")) is not list:
            raise ResponseError("page must be an object with a data array")
        rows = envelope["data"]
        count += len(rows)
        if count > _MAX_ROWS:
            raise ResponseError("metagraph exceeds 400 actual records")
        subnet = _single_subnet(rows, subnet)
    merger = PageMerger(_ENDPOINT, budget)
    for envelope in envelopes:
        merger.reserve_next_page()
        merger.accept(envelope)
    return merger.finish_query()


def fetch_metagraph(
    transport: http.Transport,
    *,
    netuid: int,
    page_count: int,
    limit: int = 1,
    allow_live: bool = False,
) -> CompletedQuery:
    """Execute only the explicit one/two-page plan on an existing transport.

    Never probe finish_query() for progress: early completion poisons a merger.
    No retry, new allowance, replacement Budget, endpoint or subnet traversal.
    """
    if allow_live is not True:
        raise http.TransportError("live metagraph fetching is disabled")
    _netuid(netuid)
    if type(page_count) is not int or not 1 <= page_count <= _MAX_PAGES:
        raise ResponseError("page_count must be one or two")
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ResponseError("limit must be an integer from 1 to 200")
    if page_count * limit > _MAX_ROWS:
        raise ResponseError("planned metagraph exceeds 400 records")
    if not isinstance(transport, http.Transport):
        raise ResponseError("Transport is required")
    session = transport.pages(_ENDPOINT, http.Query(netuid=netuid, limit=limit))
    for _ in range(page_count):
        session.fetch_next_page(allow_live=True)
    query = session.finish_query()
    if query.total_items > _MAX_ROWS:
        raise ResponseError("metagraph exceeds 400 actual records")
    _single_subnet(query.rows, netuid)
    return query


def analyze_metagraph(
    query: CompletedQuery,
    *,
    netuid: int,
    expected_block: int | None = None,
    loaded_at_utc=None,
    compute_vtrust: bool = True,
    incentive_filters=None,
) -> MetagraphAnalysis:
    """Normalize first, compute requested metrics, publish no partial analysis.

    Existing mapper functions own content checks and Decimal calculations.
    They may repeat normalization, but never pagination for CompletedQuery.
    """
    if type(query) is not CompletedQuery:
        raise ResponseError("CompletedQuery is required")
    if query.endpoint != _ENDPOINT:
        raise ResponseError("metagraph endpoint is required")
    if query.total_pages > _MAX_PAGES or query.total_items > _MAX_ROWS:
        raise ResponseError("completed metagraph exceeds operation limits")
    _netuid(netuid)
    if type(compute_vtrust) is not bool:
        raise ResponseError("compute_vtrust must be boolean")
    batch = normalize_pages(
        _ENDPOINT,
        query,
        netuid=netuid,
        expected_block=expected_block,
        loaded_at_utc=loaded_at_utc,
    )
    average = (
        vtrust_avg(query, netuid=netuid, expected_block=expected_block) if compute_vtrust else None
    )
    bounds = (
        incentive_bounds(
            query,
            netuid=netuid,
            filters=incentive_filters,
            expected_block=expected_block,
        )
        if incentive_filters is not None
        else None
    )
    return MetagraphAnalysis(batch, average, compute_vtrust, bounds)
