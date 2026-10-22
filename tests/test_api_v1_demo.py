"""Public demo runs real offline orchestration with no sockets or subprocesses."""

import json
import os
import runpy
import sys
from copy import deepcopy
from decimal import Decimal, localcontext
from fractions import Fraction
from importlib.metadata import PackageNotFoundError, distribution
from importlib.resources import files

import pytest

from taostats_pipeline import api_v1_demo as demo
from taostats_pipeline import api_v1_orchestration as orchestration
from taostats_pipeline import api_v1_transport as transport


@pytest.fixture(autouse=True)
def forbid_live_and_credentials(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("offline demo attempted a live or publication path")

    monkeypatch.setattr(transport, "Transport", forbidden)
    monkeypatch.setattr(orchestration, "fetch_metagraph", forbidden)
    environment_type = type(os.environ)
    original = environment_type.__getitem__

    def guarded_getitem(environment, name):
        if name == "TAOSTATS_API_KEY" or name.startswith("GOOGLE_"):
            pytest.fail("offline demo read a credential variable")
        return original(environment, name)

    monkeypatch.setattr(environment_type, "__getitem__", guarded_getitem)


def fixture_data():
    return json.loads(
        files("taostats_pipeline")
        .joinpath("fixtures")
        .joinpath("metagraph-demo.json")
        .read_text(encoding="utf-8")
    )


def assert_safe_error(output):
    report = json.loads(output)
    assert set(report) == {"status", "mode", "synthetic", "message"}
    assert report["status"] == "error"
    assert report["mode"] == "offline"
    assert report["synthetic"] is True
    return report


def test_real_demo_json_and_independently_calculated_decimal(capsys):
    source = fixture_data()
    assert source["metadata"]["synthetic"] is True
    rows = [row for envelope in source["envelopes"] for row in envelope["data"]]
    chosen = [
        Fraction(row["validator_trust"])
        for row in rows
        if row["active"] is True and row["validator_permit"] is True
    ]
    assert len(rows) == 6 and len(chosen) == 4
    assert Fraction(0) in chosen
    # Rational arithmetic, independent of the production Decimal mean/context.
    expected = sum(chosen, Fraction()) / len(chosen)
    assert expected == Fraction(35000000000000000000000000001, 10**29)
    with localcontext() as context:
        context.prec = 100
        expected_text = str(Decimal(expected.numerator) / Decimal(expected.denominator))
    assert demo.main([]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    report = json.loads(captured.out)
    assert report == {
        "status": "ok",
        "mode": "offline",
        "synthetic": True,
        "message": "Synthetic demo only; not real Bittensor data.",
        "netuid": 7,
        "block": 1000,
        "record_count": 6,
        "vtrust_avg": expected_text,
        "vtrust_computed": True,
    }
    assert type(report["vtrust_avg"]) is str
    assert captured.out.isascii()
    assert source["metadata"]["netuid"] == report["netuid"]
    assert source["metadata"]["expected_block"] == report["block"]
    assert "synthetic" not in rows[0] and "metadata" not in source["envelopes"][0]


def test_actual_complete_then_analyze_without_replacing_calculations(monkeypatch, capsys):
    calls = []
    real_complete = orchestration.complete_metagraph
    real_analyze = orchestration.analyze_metagraph

    def complete(envelopes, *, budget):
        calls.append("complete")
        query = real_complete(envelopes, budget=budget)
        assert (budget.requests, budget.pages) == (2, 2)
        return query

    def analyze(query, **kwargs):
        calls.append("analyze")
        assert kwargs == {"netuid": 7, "expected_block": 1000}
        return real_analyze(query, **kwargs)

    monkeypatch.setattr(demo, "complete_metagraph", complete)
    monkeypatch.setattr(demo, "analyze_metagraph", analyze)
    assert demo.main([]) == 0
    assert calls == ["complete", "analyze"]
    assert json.loads(capsys.readouterr().out)["vtrust_computed"] is True


def test_bundled_fixture_works_from_other_directory_and_creates_no_results(
    tmp_path,
    monkeypatch,
    capsys,
):
    monkeypatch.chdir(tmp_path)
    assert list(tmp_path.iterdir()) == []
    assert demo.main([]) == 0
    first = capsys.readouterr().out
    assert demo.main([]) == 0
    assert capsys.readouterr().out == first  # No current timestamps or variable output.
    assert json.loads(first)["status"] == "ok"
    assert list(tmp_path.iterdir()) == []


def test_module_entrypoint_with_installed_package_offline(tmp_path, monkeypatch, capsys):
    try:
        installed = distribution("taostats-pipeline")
    except PackageNotFoundError:
        pytest.skip("package distribution is not installed in this test environment")
    assert installed.metadata["Name"] == "taostats-pipeline"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["taostats_pipeline.api_v1_demo"])
    # Execute the same module __main__ guard as `py -m`, without a child process.
    monkeypatch.delitem(sys.modules, "taostats_pipeline.api_v1_demo")
    with pytest.raises(SystemExit) as stopped:
        runpy.run_module("taostats_pipeline.api_v1_demo", run_name="__main__", alter_sys=True)
    assert stopped.value.code == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out)["status"] == "ok"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "argv",
    [
        ["--live"],
        ["--allow-live"],
        ["--input", "private-placeholder"],
        ["--url", "https://example.invalid/"],
        ["--api-key", "sensitive-placeholder"],
        ["--credentials", "sensitive-placeholder"],
        ["--apply"],
        ["--output", "result.json"],
        ["publish-sheets"],
        ["--help", "--live"],
        ["https://example.invalid/"],
    ],
)
def test_unsupported_arguments_are_safe_json_and_never_load_data(monkeypatch, capsys, argv):
    def forbidden():
        pytest.fail("unsupported argument reached fixture loading")

    monkeypatch.setattr(demo, "_load_fixture", forbidden)
    assert demo.main(argv) == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    assert_safe_error(captured.out)
    assert "sensitive-placeholder" not in captured.out
    assert "private-placeholder" not in captured.out
    assert "https://" not in captured.out
    assert "result.json" not in captured.out


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_help_does_not_process_fixture(monkeypatch, capsys, flag):
    def forbidden():
        pytest.fail("help reached fixture processing")

    monkeypatch.setattr(demo, "_load_fixture", forbidden)
    with pytest.raises(SystemExit) as stopped:
        demo.main([flag])
    assert stopped.value.code == 0
    assert "taostats_pipeline.api_v1_demo" in capsys.readouterr().out


@pytest.mark.parametrize("synthetic", [False, None, 1, "true"])
def test_fixture_must_explicitly_declare_synthetic(monkeypatch, capsys, synthetic):
    source = fixture_data()
    source["metadata"]["synthetic"] = synthetic
    monkeypatch.setattr(demo, "_load_fixture", lambda: source)
    assert demo.main([]) == 1
    assert_safe_error(capsys.readouterr().out)


@pytest.mark.parametrize(
    "failure",
    [
        "missing_metadata",
        "missing_block",
        "negative_block",
        "null_block",
        "wrong_netuid",
        "incomplete",
        "duplicate_uid",
        "wrong_block",
        "missing_trust",
        "float_trust",
    ],
)
def test_corrupt_or_incomplete_fixture_has_no_partial_report(monkeypatch, capsys, failure):
    source = deepcopy(fixture_data())
    if failure == "missing_metadata":
        del source["metadata"]
    elif failure == "missing_block":
        del source["metadata"]["expected_block"]
    elif failure == "negative_block":
        source["metadata"]["expected_block"] = -1
    elif failure == "null_block":
        source["metadata"]["expected_block"] = None
    elif failure == "wrong_netuid":
        source["metadata"]["netuid"] = 8
    elif failure == "incomplete":
        source["envelopes"].pop()
    elif failure == "duplicate_uid":
        source["envelopes"][1]["data"][0]["uid"] = 0
    elif failure == "wrong_block":
        source["envelopes"][1]["data"][0]["block_number"] = 1001
    elif failure == "missing_trust":
        del source["envelopes"][0]["data"][0]["validator_trust"]
    else:
        source["envelopes"][0]["data"][0]["validator_trust"] = 0.1
    monkeypatch.setattr(demo, "_load_fixture", lambda: source)
    assert demo.main([]) == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    assert_safe_error(captured.out)


@pytest.mark.parametrize("text", ["{malformed-json", "[]", "null", '"sensitive-placeholder"'])
def test_malformed_resource_text_never_echoed(monkeypatch, capsys, text):
    class Resource:
        def joinpath(self, part):
            return self

        def read_text(self, *, encoding):
            assert encoding == "utf-8"
            return text

    monkeypatch.setattr(demo, "files", lambda package: Resource())
    assert demo.main([]) == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    assert_safe_error(captured.out)
    assert "malformed-json" not in captured.out
    assert "sensitive-placeholder" not in captured.out


def test_missing_resource_returns_safe_error_without_creating_files(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(demo, "files", lambda package: tmp_path)
    assert demo.main([]) == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    assert_safe_error(captured.out)
    assert str(tmp_path) not in captured.out
    assert list(tmp_path.iterdir()) == []


def test_complete_empty_fixture_serializes_missing_mean_as_null(monkeypatch, capsys):
    source = fixture_data()
    source["envelopes"] = [
        {
            "data": [],
            "pagination": {
                "current_page": 1,
                "per_page": 1,
                "total_items": 0,
                "total_pages": 0,
                "next_page": None,
                "prev_page": None,
            },
        }
    ]
    monkeypatch.setattr(demo, "_load_fixture", lambda: source)
    assert demo.main([]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["vtrust_avg"] is None
    assert report["vtrust_computed"] is True
    assert report["record_count"] == 0
    assert report["block"] is None  # Empty selection cannot prove a block.
