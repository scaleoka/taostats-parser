"""Decimal values preserve source precision; percentages use percentage points."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class SubnetSnapshot:
    netuid: int
    collected_at_utc: str
    subnet_name: str | None = None
    emission: Decimal | None = None
    price: Decimal | None = None
    market_cap: Decimal | None = None
    volume_24h: Decimal | None = None
    liquidity: Decimal | None = None
    root_proportion: Decimal | None = None
    sentiment: str | None = None
    price_change_1h: Decimal | None = None
    price_change_24h: Decimal | None = None
    price_change_1w: Decimal | None = None
    price_change_1m: Decimal | None = None


@dataclass(frozen=True)
class Metadata:
    netuid: int
    collected_at_utc: str
    subnet_name: str | None = None
    registration_date: str | None = None
    network_key: str | None = None
    discord_url: str | None = None
    github_url: str | None = None
    registration_cost: Decimal | None = None


@dataclass(frozen=True)
class ValidatorMetrics:
    netuid: int
    collected_at_utc: str
    vtrust_avg: Decimal | None = None
    incentive_orange_max: Decimal | None = None
    incentive_orange_min: Decimal | None = None
    incentive_green_max: Decimal | None = None
    incentive_green_min: Decimal | None = None
