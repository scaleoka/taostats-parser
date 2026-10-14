"""Characterize current parsing behavior using invented HTML only."""

import sys

import pytest


def test_subnet_table_fields_and_skipped_column(parsers, html_fixture):
    assert parsers.main.parse_table(html_fixture("subnets.html")) == [
        [
            "1",
            "42",
            "Example Subnet",
            "2%",
            "0.123456",
            "+1%",
            "+2%",
            "-3%",
            "+4%",
            "100",
            "20",
            "30",
            "0.5",
            "neutral",
        ]
    ]


def test_missing_table_reports_error(parsers):
    with pytest.raises(RuntimeError, match="taostats-table"):
        parsers.main.parse_table("<html></html>")


def test_empty_table(parsers):
    assert parsers.main.parse_table('<table id="taostats-table"><tbody></tbody></table>') == []


def test_metagraph_fields(parsers, html_fixture):
    assert parsers.subnets_full_parser.parse_metagraph(html_fixture("metagraph.html")) == [
        "42",
        "Example Subnet",
        "2025-01-01",
        "synthetic-account",
        "https://example.invalid/chat",
        "https://example.invalid/code",
        "2.5",
        "0.123456",
        "10.5",
    ]


def test_missing_metagraph_fields(parsers):
    assert parsers.subnets_full_parser.parse_metagraph("<html></html>") == [""] * 9


@pytest.mark.parametrize("module", ["subnets_full_parser", "subnets_vtrust_incentive"])
def test_subnet_count(parsers, module):
    assert getattr(parsers, module).get_max_subnets("<p>Showing 1 to 10 of 42 entries</p>") == 42


@pytest.mark.parametrize("module", ["subnets_full_parser", "subnets_vtrust_incentive"])
@pytest.mark.parametrize("html", ["<html></html>", "<p>Showing no entries</p>"])
def test_missing_or_invalid_count(parsers, module, html):
    with pytest.raises(RuntimeError):
        getattr(parsers, module).get_max_subnets(html)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("Bittensor 0.123456", "0.123456"), ("no price", ""), ("0.123", "")],
)
def test_price_cleaning(parsers, text, expected):
    assert parsers.subnets_full_parser.clean_price(text) == expected


def test_metrics_aggregate_multiple_pages_and_ignore_bad_rows(parsers, html_fixture):
    html = html_fixture("metrics.html")
    assert parsers.subnets_vtrust_incentive.parse_table_for_metrics([html, html]) == [
        0.6,
        0.9,
        0.2,
        0.8,
        0.1,
    ]


@pytest.mark.parametrize("pages", [[], ["<table><tr><th>Header</th></tr></table>"]])
def test_empty_metrics(parsers, pages):
    assert parsers.subnets_vtrust_incentive.parse_table_for_metrics(pages) == [""] * 5


def test_legacy_integration_modules_are_not_imported(parsers):
    assert not any(name == "playwright" or name.startswith("playwright.") for name in sys.modules)
    assert "googleapiclient.discovery" not in sys.modules
    assert "google.oauth2.service_account" not in sys.modules
