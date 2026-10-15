"""In-memory Google boundary; global socket/process guard applies to every test."""

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from taostats_pipeline.cli import main
from taostats_pipeline.exporters.google_sheets import (
    HEADERS,
    Config,
    GoogleAPI,
    SheetsError,
    cell,
    publish,
    tables,
)
from taostats_pipeline.models import Metadata, SubnetSnapshot, ValidatorMetrics

STAMP = "2026-01-01T00:00:00Z"
CONFIG = Config("synthetic_spreadsheet_12345", "synthetic_spreadsheet_12345", "unused")
FIXTURES = Path(__file__).resolve().parents[1] / "src/taostats_pipeline/fixtures"


@pytest.fixture
def datasets():
    return {
        "subnets": [
            SubnetSnapshot(
                7, STAMP, subnet_name="=IMPORTXML(secret)", price=Decimal("0.123456789012345678901")
            )
        ],
        "metadata": [Metadata(7, STAMP)],
        "validator_metrics": [ValidatorMetrics(7, STAMP)],
    }


class MemoryAPI:
    def __init__(self):
        self.props = {
            name: {
                "title": name,
                "sheetId": index,
                "gridProperties": {"rowCount": 100, "columnCount": len(header)},
            }
            for index, (name, header) in enumerate(HEADERS.items())
        }
        self.rows = {name: [header[:]] for name, header in HEADERS.items()}
        self.writes = []
        self.fail = None
        self.committed = False
        self.corrupt_stage = False
        self.corrupt_final = False
        self.unknown = False

    def structure(self):
        return {"sheets": [{"properties": prop} for prop in self.props.values()]}

    def read(self, title):
        if self.unknown and self.committed:
            raise SheetsError("Google API failure (transport/client)")
        rows = deepcopy(self.rows[title])
        if title.startswith("_stage_") and self.corrupt_stage:
            rows[-1][0] = "corrupted"
        if self.committed and title == "subnets" and self.corrupt_final:
            rows[-1][0] = "corrupted"
        # Real Sheets omits empty trailing cells.
        for row in rows:
            while row and row[-1] == "":
                row.pop()
        return rows

    def write(self, title, rows):
        self.writes.append(("write", title))
        if self.fail == "write":
            raise SheetsError("Google API failure (403)")
        self.rows[title] = deepcopy(rows)

    def batch(self, requests):
        self.writes.append(("batch", deepcopy(requests)))
        is_commit = any("copyPaste" in r for r in requests)
        if is_commit and self.fail == "commit":
            raise SheetsError("Google API failure (403)")
        if any("deleteSheet" in r for r in requests) and self.fail == "cleanup":
            raise SheetsError("Google API failure (503)")
        if any("addSheet" in r for r in requests) and self.fail == "add":
            raise SheetsError("Google API failure (403)")
        props, rows = deepcopy(self.props), deepcopy(self.rows)

        def title(sheet_id):
            return next(name for name, prop in props.items() if prop["sheetId"] == sheet_id)

        for request in requests:
            if "addSheet" in request:
                prop = request["addSheet"]["properties"]
                props[prop["title"]] = prop
                rows[prop["title"]] = []
            elif "updateSheetProperties" in request:
                prop = request["updateSheetProperties"]["properties"]
                props[title(prop["sheetId"])].update(prop)
            elif "updateCells" in request:
                update = request["updateCells"]
                area = update["range"]
                name = title(area["sheetId"])
                if "rows" not in update:
                    rows[name] = []
                    continue
                start = area.get("startRowIndex", 0)
                values = [
                    [c["userEnteredValue"]["stringValue"] for c in row["values"]]
                    for row in update["rows"]
                ]
                while len(rows[name]) < start + len(values):
                    rows[name].append([])
                rows[name][start : start + len(values)] = values
            elif "copyPaste" in request:
                copy = request["copyPaste"]
                rows[title(copy["destination"]["sheetId"])] = deepcopy(
                    rows[title(copy["source"]["sheetId"])]
                )
            elif "deleteSheet" in request:
                name = title(request["deleteSheet"]["sheetId"])
                del props[name]
                del rows[name]
            else:
                raise AssertionError(request)
        self.props, self.rows = props, rows
        if is_commit:
            self.committed = True
            if self.fail == "lost_response" or self.unknown:
                raise SheetsError("Google API failure (transport/client)")
        return {}


@pytest.fixture
def api():
    return MemoryAPI()


def apply(datasets, api, **kwargs):
    return publish(
        datasets, apply=True, config=CONFIG, api_factory=lambda _: api, run_id="test-run", **kwargs
    )


def test_success(datasets, api):
    api.rows["subnets"] += [["old"]] * 8
    result = apply(datasets, api)
    assert result["status"] == "success"
    assert set(api.rows) == set(HEADERS)
    assert api.rows["subnets"] == tables(datasets)["subnets"]
    row = api.rows["run_status"][1]
    assert row[0] == "test-run" and row[3] == "success"
    assert row[4:7] == ["1", "1", "1"] and row[7] == ""
    commits = [r for kind, r in api.writes if kind == "batch" and any("copyPaste" in x for x in r)]
    assert len(commits) == 1
    assert len([r for r in commits[0] if "copyPaste" in r]) == 3
    assert commits[0][-1]["updateCells"]["range"]["sheetId"] == 3


def test_dry_run_never_initializes_api(datasets):
    def forbidden(_):
        pytest.fail("Dry-run initialized Google")

    result = publish(datasets, config=Config("", "", ""), api_factory=forbidden)
    assert result["status"] == "dry_run"
    assert result["remote_structure_checked"] is False


@pytest.mark.parametrize(
    "config",
    [
        Config("", "", ""),
        Config("bad/id", "bad/id", ""),
        Config(CONFIG.spreadsheet_id, "", ""),
        Config(CONFIG.spreadsheet_id, "different", ""),
    ],
)
def test_invalid_config_blocks_before_api(datasets, config):
    def forbidden(_):
        pytest.fail("Invalid config reached API")

    assert publish(datasets, apply=True, config=config, api_factory=forbidden)["status"] == "failed"


def test_missing_credentials(datasets):
    config = replace(CONFIG, credentials_file="")
    result = publish(datasets, apply=True, config=config)
    assert result["status"] == "failed"
    assert "CREDENTIALS_FILE" in result["error_summary"]


@pytest.mark.parametrize("fault", ["name", "case", "header", "extra", "full_journal"])
def test_bad_structure_blocks_all_writes(datasets, api, fault):
    if fault in ("name", "case"):
        api.props["subnets"]["title"] = "Subnets" if fault == "case" else "wrong"
    elif fault == "header":
        api.rows["metadata"][0] = ["wrong"]
    elif fault == "extra":
        api.rows["subnets"].append(["x"] * (len(HEADERS["subnets"]) + 1))
    else:
        api.props["run_status"]["gridProperties"]["rowCount"] = 1
    assert apply(datasets, api)["status"] == "failed"
    assert not api.writes


@pytest.mark.parametrize("fault", ["add", "write"])
def test_staging_failure_preserves_current(datasets, api, fault):
    before = deepcopy(api.rows)
    api.fail = fault
    result = apply(datasets, api)
    assert result["status"] == "failed"
    for name in datasets:
        assert api.rows[name] == before[name]
    assert api.rows["run_status"][1][3] == "failed"


def test_staging_verification(datasets, api):
    api.corrupt_stage = True
    assert apply(datasets, api)["status"] == "failed"
    assert not api.committed


@pytest.mark.parametrize("fault", ["commit", "cleanup", "corrupt_final", "unknown"])
def test_partial_and_uncertain_outcome(datasets, api, fault):
    if fault in ("corrupt_final", "unknown"):
        setattr(api, fault, True)
    else:
        api.fail = fault
    result = apply(datasets, api)
    assert result["status"] == "partial"
    assert result["error_summary"]
    if fault in ("corrupt_final", "cleanup"):
        assert api.rows["run_status"][1][3] == "partial"
    if fault == "unknown":
        # Do not overwrite a committed success when reads fail after a lost response.
        assert api.rows["run_status"][1][3] == "success"
    if fault == "commit":
        assert len(api.rows["run_status"]) == 1


def test_lost_response_is_reconciled_without_duplicate(datasets, api):
    api.fail = "lost_response"
    assert apply(datasets, api)["status"] == "success"
    commits = [r for kind, r in api.writes if kind == "batch" and any("copyPaste" in x for x in r)]
    assert len(commits) == 1


def test_duplicate_run_id(datasets, api):
    assert apply(datasets, api)["status"] == "success"
    count = len(api.writes)
    result = apply(datasets, api)
    assert result["status"] == "success" and result["already_published"]
    assert len(api.writes) == count
    datasets["subnets"][0] = replace(datasets["subnets"][0], subnet_name="changed")
    assert apply(datasets, api)["status"] == "failed"
    assert len(api.writes) == count


def test_failed_run_id_cannot_be_reused(datasets, api):
    api.fail = "write"
    assert apply(datasets, api)["status"] == "failed"
    api.fail = None
    assert apply(datasets, api)["status"] == "failed"
    assert not api.committed


def test_validation_precedes_api(datasets, api):
    datasets["metadata"] = []
    assert apply(datasets, api)["status"] == "failed"
    assert not api.writes


def test_serialization(datasets):
    rows = tables(datasets)["subnets"]
    assert rows[1][0] == "7"
    assert rows[1][2] == "=IMPORTXML(secret)"
    assert rows[1][4] == "0.123456789012345678901"
    assert cell(None) == "" and cell(Decimal("1E-8")) == "0.00000001"
    with pytest.raises(SheetsError):
        cell(1.5)


class APIError(Exception):
    def __init__(self, status):
        self.resp = SimpleNamespace(status=status)
        super().__init__("PRIVATE CREDENTIAL DATA")


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_retry_backoff(status):
    client = GoogleAPI.__new__(GoogleAPI)
    client.config = CONFIG
    delays = []
    client.sleeper = delays.append
    calls = []

    def execute(**kwargs):
        calls.append(kwargs)
        if len(calls) < 3:
            raise APIError(status)
        return "ok"

    assert client.execute(lambda: SimpleNamespace(execute=execute), retry=True) == "ok"
    assert delays == [0.25, 0.5]
    assert calls == [{"num_retries": 0}] * 3


@pytest.mark.parametrize(
    "status,retry,expected", [(403, True, 1), (401, True, 1), (503, True, 3), (503, False, 1)]
)
def test_access_denied_and_retry_limit(status, retry, expected):
    client = GoogleAPI.__new__(GoogleAPI)
    client.config = CONFIG
    client.sleeper = lambda _: None
    calls = []

    def execute(**kwargs):
        calls.append(kwargs)
        raise APIError(status)

    with pytest.raises(SheetsError) as exc:
        client.execute(lambda: SimpleNamespace(execute=execute), retry=retry)
    assert "PRIVATE" not in str(exc.value)
    assert len(calls) == expected


def test_write_rechecks_allowlist_before_request():
    client = GoogleAPI.__new__(GoogleAPI)
    client.config = replace(CONFIG, allowed_id="")
    with pytest.raises(SheetsError, match="allowlist"):
        client.execute(lambda: pytest.fail("Request created before guard"), write=True)


def test_api_uses_raw_and_minimal_ranges():
    client = GoogleAPI.__new__(GoogleAPI)
    client.config = CONFIG
    captured = []

    class Values:
        def update(self, **kwargs):
            captured.append(kwargs)
            return SimpleNamespace(execute=lambda **_: {})

    client.api = SimpleNamespace(values=Values)
    client.write("_stage_example", [["=1+1", "0.0001"]])
    assert captured[0]["valueInputOption"] == "RAW"
    assert captured[0]["body"]["values"][0][0] == "=1+1"


def test_cli_offline_demo_and_dry_run(tmp_path, capsys, monkeypatch):
    # conftest blocks TCP/DNS/UDP/processes, including these actual CLI paths.
    monkeypatch.delenv("GOOGLE_CREDENTIALS_FILE", raising=False)
    assert main(["demo", "--output", str(tmp_path / "demo")]) == 0
    assert main(["publish-sheets", "--input", str(FIXTURES), "--dry-run"]) == 0
    assert main(["publish-sheets", "--input", str(FIXTURES)]) == 0
    assert '"status": "dry_run"' in capsys.readouterr().out


def test_cli_apply_failure_is_nonzero(monkeypatch, capsys):
    monkeypatch.delenv("GOOGLE_SPREADSHEET_ID", raising=False)
    assert main(["publish-sheets", "--input", str(FIXTURES), "--apply"]) == 1
    assert '"status": "failed"' in capsys.readouterr().out


def test_cli_validation_before_client(tmp_path, monkeypatch):
    import taostats_pipeline.exporters.google_sheets as module

    monkeypatch.setattr(module, "publish", lambda *a, **k: pytest.fail("Invalid input published"))
    assert main(["publish-sheets", "--input", str(tmp_path), "--apply"]) == 1


def test_service_account_scope_and_safe_initialization(tmp_path, monkeypatch):
    import sys
    from types import ModuleType

    from taostats_pipeline.exporters.google_sheets import SCOPE

    marker = tmp_path / "synthetic-placeholder.txt"
    marker.touch()  # No key or JSON credentials are created.
    captured = {}
    credential = object()

    class Credentials:
        @staticmethod
        def from_service_account_file(path, *, scopes):
            captured["scopes"] = scopes
            captured["path"] = path
            return credential

    def build(service, version, **kwargs):
        captured["build"] = (service, version, kwargs)
        return SimpleNamespace(spreadsheets=lambda: "synthetic-api")

    for name in (
        "google",
        "google.oauth2",
        "google.oauth2.service_account",
        "googleapiclient",
        "googleapiclient.discovery",
    ):
        monkeypatch.setitem(sys.modules, name, ModuleType(name))
    monkeypatch.setattr(
        sys.modules["google.oauth2.service_account"], "Credentials", Credentials, raising=False
    )
    monkeypatch.setattr(sys.modules["googleapiclient.discovery"], "build", build, raising=False)
    config = replace(CONFIG, credentials_file=str(marker))
    assert GoogleAPI(config).api == "synthetic-api"
    assert captured["scopes"] == [SCOPE]
    assert captured["build"][0:2] == ("sheets", "v4")
    assert captured["build"][2]["static_discovery"] is True

    def rejected(*args, **kwargs):
        raise ValueError("PRIVATE KEY CONTENT")

    monkeypatch.setattr(Credentials, "from_service_account_file", rejected)
    with pytest.raises(SheetsError) as exc:
        GoogleAPI(config)
    assert "PRIVATE" not in str(exc.value) and str(marker) not in str(exc.value)


def test_invalid_text_field_and_strict_mode(datasets, api):
    datasets["metadata"][0] = replace(datasets["metadata"][0], subnet_name=123)
    assert apply(datasets, api)["status"] == "failed"
    assert not api.writes
    datasets["metadata"][0] = Metadata(7, STAMP)
    assert apply(datasets, api, strict=True)["status"] == "failed"
    assert not api.writes


def test_failure_to_record_failure_remains_nonzero(datasets, api):
    original = api.batch

    def fail_journal(requests):
        if any("updateCells" in r for r in requests):
            raise SheetsError("Google API failure (403)")
        return original(requests)

    api.batch = fail_journal
    api.fail = "write"
    result = apply(datasets, api)
    assert result["status"] == "failed"
    assert result["journal_error"]
