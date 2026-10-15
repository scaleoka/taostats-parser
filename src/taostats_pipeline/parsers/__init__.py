"""Pure parsers for the legacy HTML layouts. Unknown layouts fail closed."""

import re
from decimal import Decimal

from bs4 import BeautifulSoup

from ..models import Metadata, SubnetSnapshot, ValidatorMetrics


class ParseError(ValueError):
    pass


def number(text: str, *, percent: bool = False, currency: bool = False) -> Decimal | None:
    value = text.strip().replace("−", "-")
    if value in {"", "—", "N/A"}:
        return None
    if currency:
        value = re.sub(r"^(?:Bittensor|[$τ])\s*", "", value).strip()
    if percent and value.endswith("%"):
        value = value[:-1].strip()
    if not re.fullmatch(r"[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", value):
        raise ParseError(f"Invalid or unsupported number: {text!r}")
    return Decimal(value.replace(",", ""))


def netuid(text: str) -> int:
    if not re.fullmatch(r"\d+", text.strip()):
        raise ParseError(f"Missing or invalid netuid: {text!r}")
    return int(text)


def table_rows(html: str, *, snapshot: bool = False):
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table", id="taostats-table") if snapshot else soup.find_all("table")
    if len(tables) != 1:
        raise ParseError("Expected exactly one data table")
    table = tables[0]
    if snapshot and table.find("tbody") is None:
        raise ParseError("Missing snapshot tbody")
    rows = []
    for row in table.find_all("tr"):
        cells = row.find_all("td", recursive=False)
        if cells:
            rows.append(cells)
        elif not row.find("th"):
            raise ParseError("Malformed table row")
    return rows


def parse_snapshots(html: str, timestamp: str) -> list[SubnetSnapshot]:
    records = []
    for cells in table_rows(html, snapshot=True):
        if len(cells) != 14:
            raise ParseError("Snapshot row must have 14 cells")
        names = cells[1].find_all("p")
        if len(names) != 2:
            raise ParseError("Snapshot name/netuid layout changed")
        values = [cell.get_text(strip=True) for cell in cells]
        records.append(
            SubnetSnapshot(
                netuid=netuid(names[1].get_text(strip=True)),
                collected_at_utc=timestamp,
                subnet_name=names[0].get_text(strip=True) or None,
                emission=number(values[2], percent=True),
                price=number(values[3], currency=True),
                price_change_1h=number(values[4], percent=True),
                price_change_24h=number(values[5], percent=True),
                price_change_1w=number(values[6], percent=True),
                price_change_1m=number(values[7], percent=True),
                market_cap=number(values[8], currency=True),
                volume_24h=number(values[9], currency=True),
                liquidity=number(values[10], currency=True),
                root_proportion=number(values[11], percent=True),
                sentiment=values[13] or None,
            )
        )
    return records


def parse_metadata(html: str, timestamp: str) -> Metadata:
    soup = BeautifulSoup(html, "html.parser")

    def label_value(label):
        labels = soup.find_all("p", string=label)
        if len(labels) > 1:
            raise ParseError(f"Duplicate label: {label}")
        sibling = labels[0].find_next_sibling("p") if labels else None
        return sibling.get_text(strip=True) if sibling else ""

    title = soup.find("p", class_=re.compile(r"font-bold.*text-2xl"))
    account = soup.find("a", href=re.compile(r"^/account/"))

    def social(name):
        icon = soup.find("img", src=f"/images/logo/{name}.svg")
        return icon.parent.get("href") if icon and icon.parent.name == "a" else None

    cost_label = soup.find("p", string=re.compile(r"^Reg Cost$"))
    cost = None
    if cost_label:
        block = cost_label.find_parent("div", class_=re.compile("flex"))
        value = block.find_next_sibling("div") if block else None
        if value is None:
            raise ParseError("Registration cost layout changed")
        cost = number(value.get_text(" ", strip=True), currency=True)
    return Metadata(
        netuid=netuid(label_value("Netuid:")),
        collected_at_utc=timestamp,
        subnet_name=title.get_text(strip=True) if title else None,
        registration_date=label_value("Reg:") or None,
        network_key=account["href"].removeprefix("/account/") if account else None,
        discord_url=social("discord"),
        github_url=social("github"),
        registration_cost=cost,
    )


def parse_metrics(pages: list[str], subnet_id: int, timestamp: str) -> ValidatorMetrics:
    # subnet_id comes from the explicit input manifest, never from a row number.
    subnet_id = netuid(str(subnet_id))
    if not pages:
        raise ParseError(f"No metric pages for netuid {subnet_id}")
    groups = {"validator": [], "orange": [], "green": []}
    for html in pages:
        rows = table_rows(html)
        if not rows:
            raise ParseError(f"Empty metrics table for netuid {subnet_id}")
        for cells in rows:
            if len(cells) != 9:
                raise ParseError("Metrics row must have 9 cells")
            icon = cells[1].find("svg")
            classes = set(icon.get("class", [])) if icon else set()
            if {"lucide-shield", "text-indigo-400"} <= classes:
                group, index = "validator", 5
            elif {"lucide-pickaxe", "text-[#f90]"} <= classes:
                group, index = "orange", 8
            elif {"lucide-pickaxe", "text-[#00dbbc]"} <= classes:
                group, index = "green", 8
            else:
                raise ParseError("Unrecognized metric role/icon")
            value = number(cells[index].get_text(strip=True))
            if value is not None:
                if value < 0:
                    raise ParseError("Metric values must be nonnegative")
                groups[group].append(value)
    validators, orange, green = (groups[key] for key in ("validator", "orange", "green"))
    return ValidatorMetrics(
        subnet_id,
        timestamp,
        round(sum(validators) / len(validators), 6) if validators else None,
        max(orange) if orange else None,
        min(orange) if orange else None,
        max(green) if green else None,
        min(green) if green else None,
    )
