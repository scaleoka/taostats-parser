"""New package tests run under the global network/process guard in conftest."""

import csv
import json
import shutil
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from taostats_pipeline import exporters
from taostats_pipeline.cli import main
from taostats_pipeline.models import Metadata, SubnetSnapshot, ValidatorMetrics
from taostats_pipeline.parsers import (
    ParseError,
    number,
    parse_metadata,
    parse_metrics,
    parse_snapshots,
)
from taostats_pipeline.pipeline import run
from taostats_pipeline.validation import validate

FIXTURES = Path(__file__).resolve().parents[1] / "src/taostats_pipeline/fixtures"
STAMP = "2026-01-01T00:00:00Z"


@pytest.fixture
def local_input(tmp_path):
    return Path(shutil.copytree(FIXTURES, tmp_path / "input"))


def data():
    return {
        "subnets": [SubnetSnapshot(i, STAMP) for i in (7, 42)],
        "metadata": [Metadata(i, STAMP) for i in (7, 42)],
        "validator_metrics": [ValidatorMetrics(i, STAMP) for i in (7, 42)],
    }


def read(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_all_categories():
    snapshots = parse_snapshots(read("subnets.html"), STAMP)
    assert {r.netuid for r in snapshots} == {7, 42}
    record = snapshots[0]
    assert record.subnet_name == "Example Subnet"
    assert record.emission == Decimal("2")
    assert record.price == Decimal("0.123456")
    assert record.market_cap == 100 and record.volume_24h == 20 and record.liquidity == 30
    assert record.root_proportion == Decimal("0.5") and record.sentiment == "neutral"
    assert [
        record.price_change_1h,
        record.price_change_24h,
        record.price_change_1w,
        record.price_change_1m,
    ] == [1, 2, -3, 4]
    meta = parse_metadata(read("metadata-42.html"), STAMP)
    assert meta.netuid == 42 and meta.registration_cost == Decimal("10.5")
    assert meta.registration_date == "2025-01-01"
    assert meta.network_key == "synthetic-account"
    assert meta.discord_url == "https://example.invalid/chat"
    assert meta.github_url == "https://example.invalid/code"
    metrics = parse_metrics([read("metrics.html")] * 2, 42, STAMP)
    assert metrics == ValidatorMetrics(
        42, STAMP, Decimal("0.6"), Decimal("0.9"), Decimal("0.2"), Decimal("0.8"), Decimal("0.1")
    )


@pytest.mark.parametrize(
    "html", ["", "<html>broken</html>", '<table id="taostats-table"><tr><td>x</td></tr></table>']
)
def test_damaged_html(html):
    with pytest.raises(ParseError):
        parse_snapshots(html, STAMP)
    with pytest.raises(ParseError):
        parse_metadata(html, STAMP)
    with pytest.raises(ParseError):
        parse_metrics([html], 42, STAMP)


def test_empty_tables_and_pages():
    assert parse_snapshots('<table id="taostats-table"><tbody></tbody></table>', STAMP) == []
    for pages in ([], ["<table><tr><th>Header</th></tr></table>"]):
        with pytest.raises(ParseError):
            parse_metrics(pages, 42, STAMP)


def test_optional_values_are_explicit():
    meta = parse_metadata("<p>Netuid:</p><p>42</p>", STAMP)
    assert meta.subnet_name is None and meta.registration_cost is None
    snapshot = parse_snapshots(read("subnets.html").replace(">2%<", ">—<"), STAMP)[0]
    assert snapshot.emission is None
    metrics = parse_metrics(
        [read("metrics.html").replace("0.4", "N/A").replace("0.8", "N/A")], 42, STAMP
    )
    assert metrics.vtrust_avg is None


@pytest.mark.parametrize(
    "value", ["ERROR", "NaN", "Infinity", "1,23", "1.2M", "1.2.3", "$1 τ", "12 USD"]
)
def test_invalid_numbers(value):
    with pytest.raises(ParseError):
        number(value, currency=True, percent=True)


def test_number_conversion_is_explicit():
    assert number("Bittensor 1,234.567890", currency=True) == Decimal("1234.567890")
    assert number("−2.5%", percent=True) == Decimal("-2.5")
    assert number("$12.50", currency=True) == Decimal("12.50")
    with pytest.raises(ParseError):
        number("5%")


def test_noncontiguous_and_duplicate_ids():
    datasets = data()
    assert validate(datasets, {7, 42}).status == "ok"
    datasets["metadata"].append(datasets["metadata"][0])
    result = validate(datasets, {7, 42})
    assert result.status == "error" and result.duplicate_ids["metadata"] == [7]


def test_missing_unexpected_and_join_coverage():
    datasets = data()
    datasets["metadata"] = [Metadata(99, STAMP)]
    result = validate(datasets, {7, 42})
    assert result.missing_ids["metadata"] == [7, 42]
    assert result.unexpected_ids["metadata"] == [99]
    assert validate(datasets).missing_ids["subnets"] == [99]
    datasets["metadata"] = []
    assert validate(datasets).status == "error"


@pytest.mark.parametrize(
    "changes",
    [
        {"netuid": None},
        {"netuid": True},
        {"netuid": -1},
        {"price": Decimal("NaN")},
        {"price": "ERROR"},
        {"price": Decimal("-1")},
        {"collected_at_utc": "2026-01-01"},
        {"collected_at_utc": "bad"},
    ],
)
def test_invalid_model_values(changes):
    datasets = data()
    datasets["subnets"][0] = replace(datasets["subnets"][0], **changes)
    assert validate(datasets).status == "error"


def test_invalid_metric_ranges():
    datasets = data()
    datasets["validator_metrics"][0] = ValidatorMetrics(
        7, STAMP, incentive_green_min=Decimal("2"), incentive_green_max=Decimal("1")
    )
    assert validate(datasets).status == "error"


def test_bad_metric_rows_are_not_silently_aggregated():
    for value in ("invalid", "-0.4"):
        with pytest.raises(ParseError):
            parse_metrics([read("metrics.html").replace("0.4", value)], 42, STAMP)
    with pytest.raises(ParseError, match="Unrecognized"):
        parse_metrics([read("metrics.html").replace("lucide-shield", "unknown")], 42, STAMP)


def test_export_precision_nulls_and_csv_round_trip(tmp_path):
    datasets = data()
    datasets["subnets"][0] = replace(
        datasets["subnets"][0],
        price=Decimal("12345678901234567890.1234567890"),
        subnet_name='Synthetic, "quoted" subnet',
    )
    generation = exporters.export(datasets, tmp_path)
    rows = json.loads((generation / "subnets.json").read_text(encoding="utf-8"))
    assert rows[0]["price"] == "12345678901234567890.1234567890"
    assert rows[0]["emission"] is None
    with (generation / "subnets.csv").open(encoding="utf-8", newline="") as handle:
        csv_rows = list(csv.DictReader(handle))
    assert csv_rows[0]["subnet_name"] == 'Synthetic, "quoted" subnet'
    assert csv_rows[0]["price"] == rows[0]["price"]
    assert csv_rows[0]["emission"] == ""


def test_reproducible_file_contents(tmp_path):
    first = run(FIXTURES, tmp_path).generation
    second = run(FIXTURES, tmp_path).generation
    assert first != second
    assert {p.name: p.read_bytes() for p in first.iterdir()} == {
        p.name: p.read_bytes() for p in second.iterdir()
    }


def test_end_to_end_demo_no_network(tmp_path, capsys):
    # conftest blocks socket connections and child processes throughout this call.
    assert main(["demo", "--output", str(tmp_path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "ok"
    assert report["record_counts"] == {name: 2 for name in data()}
    pointer = json.loads((tmp_path / "current.json").read_text())
    directory = tmp_path / pointer["generation"]
    assert len(list(directory.glob("*.csv"))) == 3
    assert len(list(directory.glob("*.json"))) == 3
    snapshots = json.loads((directory / "subnets.json").read_text())
    assert {r["netuid"] for r in snapshots} == {7, 42}
    assert snapshots[0]["price"] == "0.123456"
    assert not any(name.startswith(("playwright", "googleapiclient")) for name in sys.modules)


@pytest.mark.parametrize("failure", ["write", "generation", "pointer"])
def test_export_failure_preserves_previous_generation(tmp_path, monkeypatch, failure):
    first = run(FIXTURES, tmp_path)
    previous = (tmp_path / "current.json").read_bytes()
    contents = {p.name: p.read_bytes() for p in first.generation.iterdir()}
    original_replace = exporters.os.replace
    original_write = exporters.write_dataset
    count = 0

    def fail_write(*args):
        nonlocal count
        count += 1
        original_write(*args)
        if count == 2:
            raise OSError("synthetic disk failure")

    def fail_replace(source, destination):
        if (failure == "pointer" and Path(destination).name == "current.json") or (
            failure == "generation" and Path(destination).name.startswith("run-")
        ):
            raise OSError("synthetic replace failure")
        return original_replace(source, destination)

    if failure == "write":
        monkeypatch.setattr(exporters, "write_dataset", fail_write)
    else:
        monkeypatch.setattr(exporters.os, "replace", fail_replace)
    result = run(FIXTURES, tmp_path)
    assert result.validation.status == "error"
    assert (tmp_path / "current.json").read_bytes() == previous
    assert {p.name: p.read_bytes() for p in first.generation.iterdir()} == contents
    # Once pointer replacement is attempted its outcome may be ambiguous, so
    # retain the complete generation even when this injected failure preceded it.
    assert len(list(tmp_path.iterdir())) == (3 if failure == "pointer" else 2)
    assert not list(tmp_path.glob(".staging-*"))
    assert not list(tmp_path.glob(".current-*.tmp"))


def test_parse_failure_does_not_export(local_input, tmp_path):
    output = tmp_path / "output"
    first = run(local_input, output)
    previous = (output / "current.json").read_bytes()
    (local_input / "metrics.html").write_text(read("metrics.html").replace("0.4", "invalid"))
    result = run(local_input, output)
    assert result.validation.status == "error"
    assert result.validation.missing_ids["validator_metrics"] == [7, 42]
    assert first.generation.exists() and (output / "current.json").read_bytes() == previous


def test_partial_missing_and_duplicate_manifest(local_input):
    path = local_input / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["subnets"] = [manifest["subnets"][0]] * 2
    path.write_text(json.dumps(manifest))
    report = run(local_input).validation
    assert report.missing_ids["metadata"] == [42]
    assert report.duplicate_ids["metadata"] == [7]


def test_metadata_identity_mismatch(local_input):
    path = local_input / "metadata-42.html"
    path.write_text(path.read_text().replace(">42<", ">99<"))
    result = run(local_input)
    assert result.validation.status == "error"
    assert any("differs from manifest" in error for error in result.validation.errors)


def test_cli_failure_and_validation(local_input, tmp_path, capsys):
    assert main(["validate", "--input", str(local_input)]) == 0
    (local_input / "subnets.html").write_text("broken")
    assert main(["run", "--input", str(local_input), "--output", str(tmp_path / "out")]) == 1
    assert not (tmp_path / "out").exists()
    assert '"status": "error"' in capsys.readouterr().out


@pytest.mark.parametrize("contents", ["{", "null", "[]", "{}"])
def test_bad_manifest(local_input, contents):
    (local_input / "manifest.json").write_text(contents)
    assert run(local_input).validation.status == "error"


def test_input_cannot_escape_fixture_directory(local_input):
    path = local_input / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["snapshot"] = "../outside.html"
    path.write_text(json.dumps(manifest))
    assert run(local_input).validation.status == "error"


def test_invalid_data_cannot_be_exported(tmp_path):
    datasets = data()
    datasets["metadata"] = []
    with pytest.raises(ValueError, match="Export refused"):
        exporters.export(datasets, tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("exception", [OSError, RuntimeError, KeyboardInterrupt])
def test_exception_after_pointer_replace_never_deletes_published_generation(
    tmp_path, monkeypatch, exception
):
    previous = exporters.export(data(), tmp_path)
    previous_contents = {p.name: p.read_bytes() for p in previous.iterdir()}
    original_replace = exporters.os.replace

    def replace_then_raise(source, destination):
        original_replace(source, destination)
        if Path(destination).name == "current.json":
            raise exception("synthetic failure after pointer publication")

    monkeypatch.setattr(exporters.os, "replace", replace_then_raise)
    with pytest.raises(exception, match="after pointer publication"):
        exporters.export(data(), tmp_path)
    current = tmp_path / json.loads((tmp_path / "current.json").read_text())["generation"]
    assert current != previous
    assert {p.name: p.read_bytes() for p in current.iterdir()} == previous_contents
    assert {p.name: p.read_bytes() for p in previous.iterdir()} == previous_contents
    assert len(list(current.iterdir())) == 6


@pytest.mark.parametrize("provided_pages", [0, 1, 3])
def test_metric_page_count_mismatch_blocks_export(local_input, tmp_path, provided_pages):
    output = tmp_path / "out"
    previous = run(local_input, output).generation
    pointer = (output / "current.json").read_bytes()
    path = local_input / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["subnets"][0]["expected_metric_pages"] = 2
    manifest["subnets"][0]["metrics"] = ["metrics.html"] * provided_pages
    path.write_text(json.dumps(manifest))
    result = run(local_input, output)
    assert result.validation.status == "error"
    assert result.validation.missing_ids["validator_metrics"] == [7]
    assert any(
        f"Expected 2 metric pages, got {provided_pages}" in error
        for error in result.validation.errors
    )
    assert (output / "current.json").read_bytes() == pointer
    assert previous.is_dir() and result.generation is None
    assert len(list(output.glob("run-*"))) == 1


@pytest.mark.parametrize("expected_pages", [None, True, 0, -1, "2", 2.0])
def test_invalid_expected_metric_pages(local_input, tmp_path, expected_pages):
    path = local_input / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["subnets"][0]["expected_metric_pages"] = expected_pages
    path.write_text(json.dumps(manifest))
    result = run(local_input, tmp_path / "out")
    assert result.validation.status == "error"
    assert any("positive integer" in error for error in result.validation.errors)
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("expected_pages", [None, 2])
def test_metric_page_count_optional_or_matching(local_input, tmp_path, expected_pages):
    path = local_input / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["subnets"][0]["metrics"] = ["metrics.html", "second-page.html"]
    if expected_pages is not None:
        manifest["subnets"][0]["expected_metric_pages"] = expected_pages
    (local_input / "second-page.html").write_text(read("metrics.html"))
    path.write_text(json.dumps(manifest))
    result = run(local_input, tmp_path / "out")
    assert result.validation.status == "ok" and result.generation.is_dir()


def test_matching_count_does_not_hide_missing_page_file(local_input, tmp_path):
    path = local_input / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["subnets"][0]["expected_metric_pages"] = 2
    manifest["subnets"][0]["metrics"] = ["metrics.html", "missing-page.html"]
    path.write_text(json.dumps(manifest))
    assert run(local_input, tmp_path / "out").validation.status == "error"
    assert not (tmp_path / "out").exists()


def strict_data():
    datasets = data()
    datasets["subnets"] = [
        replace(record, emission=Decimal("0"), price=Decimal("0")) for record in datasets["subnets"]
    ]
    datasets["validator_metrics"] = [
        ValidatorMetrics(i, STAMP, *([Decimal("0")] * 5)) for i in (7, 42)
    ]
    return datasets


@pytest.mark.parametrize(
    "category,field",
    [
        ("subnets", "emission"),
        ("subnets", "price"),
        ("validator_metrics", "vtrust_avg"),
        ("validator_metrics", "incentive_orange_max"),
        ("validator_metrics", "incentive_orange_min"),
        ("validator_metrics", "incentive_green_max"),
        ("validator_metrics", "incentive_green_min"),
    ],
)
def test_strict_mode_requires_each_critical_metric(category, field):
    datasets = strict_data()
    datasets[category][0] = replace(datasets[category][0], **{field: None})
    result = validate(datasets, strict=True)
    assert result.status == "error"
    assert any(f"netuid=7.{field}: required metric missing" in error for error in result.errors)


def test_strict_accepts_zero_and_leaves_other_fields_optional():
    assert validate(strict_data(), strict=True).status == "ok"
    assert validate(data()).status == "ok"


def test_strict_export_cannot_bypass_validation(tmp_path):
    with pytest.raises(ValueError, match="required metric missing"):
        exporters.export(data(), tmp_path / "out", strict=True)
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("enable_in_manifest", [False, True])
def test_strict_pipeline_preserves_previous_output(local_input, tmp_path, enable_in_manifest):
    output = tmp_path / "out"
    previous = run(local_input, output).generation
    pointer = (output / "current.json").read_bytes()
    (local_input / "metrics.html").write_text(
        read("metrics.html").replace("0.4", "N/A").replace("0.8", "N/A")
    )
    if enable_in_manifest:
        path = local_input / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["strict_validation"] = True
        path.write_text(json.dumps(manifest))
        result = run(local_input, output)
    else:
        assert run(local_input).validation.status == "ok"
        result = run(local_input, output, strict=True)
    assert result.validation.status == "error"
    assert any("vtrust_avg: required metric missing" in error for error in result.validation.errors)
    assert (output / "current.json").read_bytes() == pointer
    assert previous.is_dir() and result.generation is None


def test_cli_strict_override(local_input, tmp_path, capsys):
    path = local_input / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["strict_validation"] = True
    path.write_text(json.dumps(manifest))
    (local_input / "metrics.html").write_text(
        read("metrics.html").replace("0.4", "N/A").replace("0.8", "N/A")
    )
    assert main(["validate", "--input", str(local_input)]) == 1
    assert main(["validate", "--input", str(local_input), "--no-strict"]) == 0
    assert main(["validate", "--input", str(local_input), "--strict"]) == 1
    assert main(["demo", "--strict", "--output", str(tmp_path / "demo")]) == 0
    assert '"status": "ok"' in capsys.readouterr().out


@pytest.mark.parametrize("value", [None, "true", 1, [], {}])
def test_manifest_strict_flag_must_be_boolean(local_input, tmp_path, value):
    path = local_input / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["strict_validation"] = value
    path.write_text(json.dumps(manifest))
    result = run(local_input, tmp_path / "out")
    assert result.validation.status == "error"
    assert any("strict_validation must be a boolean" in error for error in result.validation.errors)
    assert not (tmp_path / "out").exists()
