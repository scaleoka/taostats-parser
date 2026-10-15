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
    VerificationMismatch,
    cell,
    normalized,
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
        return deepcopy({"sheets": [{"properties": prop} for prop in self.props.values()]})

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
        while rows and not rows[-1]:
            rows.pop()
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
        props = deepcopy(self.props)
        rows = {name: [row[:] for row in values] for name, values in self.rows.items()}

        def title(sheet_id):
            found = [name for name, prop in props.items() if prop["sheetId"] == sheet_id]
            if not found:
                raise SheetsError("Google API failure (400)")
            return found[0]

        def bounds(area):
            name = title(area["sheetId"])
            grid = props[name]["gridProperties"]
            r0, c0 = area.get("startRowIndex", 0), area.get("startColumnIndex", 0)
            r1 = area.get("endRowIndex", grid["rowCount"])
            c1 = area.get("endColumnIndex", grid["columnCount"])
            if not (0 <= r0 < r1 <= grid["rowCount"] and 0 <= c0 < c1 <= grid["columnCount"]):
                raise SheetsError("Google API failure (400)")
            return name, r0, r1, c0, c1

        def put(name, r, c, value):
            while len(rows[name]) <= r:
                rows[name].append([])
            while len(rows[name][r]) <= c:
                rows[name][r].append("")
            rows[name][r][c] = value

        for request in requests:
            if "addSheet" in request:
                prop = request["addSheet"]["properties"]
                props[prop["title"]] = prop
                rows[prop["title"]] = []
            elif "updateSheetProperties" in request:
                update = request["updateSheetProperties"]
                prop = update["properties"]
                target = props[title(prop["sheetId"])]["gridProperties"]
                for mask in update["fields"].split(","):
                    assert mask in ("gridProperties.rowCount", "gridProperties.columnCount")
                    key = mask.split(".")[1]
                    target[key] = prop["gridProperties"][key]
            elif "updateCells" in request:
                update = request["updateCells"]
                name, r0, r1, c0, c1 = bounds(update["range"])
                assert update["fields"] == "userEnteredValue"
                # Missing row data clears values throughout the specified range.
                for r in range(r0, min(r1, len(rows[name]))):
                    for c in range(c0, min(c1, len(rows[name][r]))):
                        rows[name][r][c] = ""
                for r, row in enumerate(update.get("rows", []), start=r0):
                    for c, value in enumerate(row["values"], start=c0):
                        put(name, r, c, value["userEnteredValue"]["stringValue"])
            elif "copyPaste" in request:
                copy = request["copyPaste"]
                source, sr0, sr1, sc0, sc1 = bounds(copy["source"])
                dest, dr0, dr1, dc0, dc1 = bounds(copy["destination"])
                sh, sw = sr1 - sr0, sc1 - sc0
                height, width = max(sh, dr1 - dr0), max(sw, dc1 - dc0)
                assert copy["pasteType"] == "PASTE_VALUES"
                source_rows = deepcopy(rows[source])
                for r in range(height):
                    for c in range(width):
                        sr, sc = sr0 + r % sh, sc0 + c % sw
                        value = (
                            source_rows[sr][sc]
                            if (sr < len(source_rows) and sc < len(source_rows[sr]))
                            else ""
                        )
                        put(dest, dr0 + r, dc0 + c, value)
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
    assert normalized(api.read("subnets"), len(HEADERS["subnets"])) == tables(datasets)["subnets"]
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


@pytest.mark.parametrize("fault", ["commit", "corrupt_final", "unknown"])
def test_partial_and_uncertain_outcome(datasets, api, fault):
    if fault in ("corrupt_final", "unknown"):
        setattr(api, fault, True)
    else:
        api.fail = fault
    result = apply(datasets, api)
    assert result["status"] == "partial"
    assert result["error_summary"]
    if fault == "corrupt_final":
        assert api.rows["run_status"][1][3] == "partial"
        assert result["verification"] == "mismatch"
    if fault == "unknown":
        # Do not overwrite a committed success when reads fail after a lost response.
        assert api.rows["run_status"][1][3] == "success"
        assert result["verification"] == "unavailable"
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
    assert result["status"] == "already_published" and result["already_published"]
    assert result["current_data_status"] == "matches"
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


@pytest.mark.parametrize("lost_cleanup_response", [False, True])
def test_cleanup_warning_preserves_publication_and_journal(datasets, api, lost_cleanup_response):
    original = api.batch
    if lost_cleanup_response:

        def lose_response(requests):
            response = original(requests)
            if any("deleteSheet" in request for request in requests):
                raise SheetsError("Google API failure (transport/client)")
            return response

        api.batch = lose_response
    else:
        api.fail = "cleanup"
    result = apply(datasets, api)
    assert result["status"] == "success"
    assert result["current_data_status"] == "matches"
    assert result["verification"] == "matched"
    assert result["error_summary"] == ""
    assert result["cleanup_warning"] and len(result["staging_sheet_ids"]) == 3
    assert api.rows["run_status"][1][3] == "success"
    assert len(api.rows["run_status"]) == 2
    # No separate downgrade of the journal after cleanup fails.
    assert not any(
        len(reqs) == 1 and "updateCells" in reqs[0] for kind, reqs in api.writes if kind == "batch"
    )


def test_data_read_unavailable_is_not_confirmed_corruption(datasets, api):
    original = api.read

    def unavailable(title):
        if api.committed and title == "subnets":
            raise SheetsError("Google API failure (503)")
        return original(title)

    api.read = unavailable
    result = apply(datasets, api)
    assert result["status"] == "partial"
    assert result["verification"] == "unavailable"
    assert result["current_data_status"] == "unverified"
    assert api.rows["run_status"][1][3] == "success"
    assert "cleanup_warning" not in result
    assert not any("deleteSheet" in r for kind, reqs in api.writes if kind == "batch" for r in reqs)


@pytest.mark.parametrize(
    "rows,expected",
    [
        ([], []),
        ([[], ["", ""], []], []),
        ([["7", "", "0.10"]], [["7", "", "0.10"]]),
        (
            [["7"], [], ["42", "name"], [], ["", "", "", ""]],
            [["7", "", ""], ["", "", ""], ["42", "name", ""]],
        ),
        ([["7", "", "", "", ""]], [["7", "", ""]]),
        ([["=1+1", "+001", "001"]], [["=1+1", "+001", "001"]]),
    ],
)
def test_actual_values_response_blank_shapes(rows, expected):
    assert normalized(rows, 3) == expected


@pytest.mark.parametrize(
    "rows",
    [
        [[7]],
        [[7.0]],
        [[0]],
        [[0.1]],
        [[True]],
        [[False]],
        [[None]],
        [["7", "", "", "unexpected"]],
        [["7"], ["", "", "", 0]],
        ["7"],
        {"values": []},
        None,
    ],
)
def test_actual_values_response_rejects_type_coercion_and_extra_data(rows):
    with pytest.raises(VerificationMismatch):
        normalized(rows, 3)


@pytest.mark.parametrize("number", [7, 7.0, True, None])
@pytest.mark.parametrize("phase", ["staging", "published"])
def test_numeric_or_nontext_cell_is_mismatch(datasets, api, number, phase):
    original = api.read

    def numeric_response(title):
        rows = original(title)
        if (phase == "staging" and title.startswith("_stage_")) or (
            phase == "published" and api.committed and title == "subnets"
        ):
            rows[1][0] = number
        return rows

    api.read = numeric_response
    result = apply(datasets, api)
    assert result["status"] == ("failed" if phase == "staging" else "partial")
    assert result["verification"] == "mismatch"
    assert api.committed == (phase == "published")
    assert api.rows["run_status"][1][3] == ("failed" if phase == "staging" else "partial")


@pytest.mark.parametrize(
    "response,expected",
    [
        ({"range": "'subnets'!A1:N100", "majorDimension": "ROWS"}, []),
        ({"values": [["7", "", "0.10"], [], ["42"]]}, [["7", "", "0.10"], [], ["42"]]),
        ({"values": [[7, 0.1, True, "007"]]}, [[7, 0.1, True, "007"]]),
    ],
)
def test_google_read_preserves_raw_json_types_and_requests_rows(response, expected):
    client = GoogleAPI.__new__(GoogleAPI)
    client.config = CONFIG
    calls = []

    class Values:
        def get(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(execute=lambda **_: response)

    client.api = SimpleNamespace(values=Values)
    assert client.read("subnets") == expected
    assert calls[0]["majorDimension"] == "ROWS"
    assert calls[0]["valueRenderOption"] == "UNFORMATTED_VALUE"
    assert calls[0]["range"] == "'subnets'"


def test_historical_run_after_new_publication_reports_different_current_data(datasets, api):
    old = deepcopy(datasets)
    assert apply(old, api)["status"] == "success"
    datasets["subnets"][0] = replace(datasets["subnets"][0], subnet_name="new-generation")
    assert (
        publish(datasets, apply=True, config=CONFIG, api_factory=lambda _: api, run_id="new-run")[
            "status"
        ]
        == "success"
    )
    writes = len(api.writes)
    result = apply(old, api)
    assert result["status"] == "already_published"
    assert result["historical_status"] == "success"
    assert result["current_data_status"] == "differs"
    assert result["verification"] == "mismatch"
    assert len(api.writes) == writes
    assert api.rows["subnets"][1][2] == "new-generation"
    assert [row[3] for row in api.rows["run_status"][1:]] == ["success", "success"]


def test_same_content_in_later_run_does_not_claim_new_publication(datasets, api):
    assert apply(datasets, api)["status"] == "success"
    assert (
        publish(datasets, apply=True, config=CONFIG, api_factory=lambda _: api, run_id="later-run")[
            "status"
        ]
        == "success"
    )
    writes = len(api.writes)
    result = apply(datasets, api)
    assert result["status"] == "already_published"
    assert result["current_data_status"] == "matches"
    assert api.rows["run_status"][-1][0] == "later-run"
    assert len(api.writes) == writes


def test_historical_run_detects_changed_cell_type(datasets, api):
    assert apply(datasets, api)["status"] == "success"
    api.rows["subnets"][1][0] = 7
    writes = len(api.writes)
    result = apply(datasets, api)
    assert result["status"] == "already_published"
    assert result["current_data_status"] == "differs"
    assert len(api.writes) == writes


def test_large_previously_populated_grids_clear_all_obsolete_rows(datasets, api):
    for name in datasets:
        api.props[name]["gridProperties"].update(rowCount=20000, columnCount=30, frozenRowCount=1)
        # Long obsolete tail, nonempty values far beyond the replacement rectangle.
        api.rows[name] += [["obsolete", "old", "tail"] for _ in range(19999)]
    result = apply(datasets, api)
    assert result["status"] == "success"
    for name, expected in tables(datasets).items():
        assert normalized(api.read(name), len(HEADERS[name])) == expected
        assert api.rows[name][0] == HEADERS[name]
        assert all(not any(row) for row in api.rows[name][len(expected) :])
        assert api.props[name]["gridProperties"] == {
            "rowCount": 20000,
            "columnCount": 30,
            "frozenRowCount": 1,
        }
    commit = next(
        reqs for kind, reqs in api.writes if kind == "batch" and any("copyPaste" in r for r in reqs)
    )
    assert not any("updateSheetProperties" in r for r in commit)
    for req in commit:
        if "copyPaste" in req:
            src, dst = req["copyPaste"]["source"], req["copyPaste"]["destination"]
            assert {k: v for k, v in src.items() if k != "sheetId"} == {
                k: v for k, v in dst.items() if k != "sheetId"
            }


def test_growing_grids_preserves_frozen_rows_and_empty_values(api):
    datasets = {
        "subnets": [SubnetSnapshot(i, STAMP) for i in range(150)],
        "metadata": [Metadata(i, STAMP) for i in range(150)],
        "validator_metrics": [ValidatorMetrics(i, STAMP) for i in range(150)],
    }
    for name in datasets:
        api.props[name]["gridProperties"].update(rowCount=1, frozenRowCount=1)
    assert apply(datasets, api)["status"] == "success"
    for name, expected in tables(datasets).items():
        assert api.props[name]["gridProperties"]["rowCount"] == 151
        assert api.props[name]["gridProperties"]["frozenRowCount"] == 1
        assert api.rows[name][0] == HEADERS[name]
        assert normalized(api.read(name), len(HEADERS[name])) == expected


@pytest.mark.parametrize(
    "change",
    [
        "rename",
        "replace_id",
        "shrink",
        "grow",
        "columns",
        "sheet_type",
        "header",
        "journal",
        "staging_size",
    ],
)
def test_changes_during_staging_block_final_batch(datasets, api, change):
    original = api.write
    changed = []

    def modify_after_staging(title, rows):
        original(title, rows)
        if changed:
            return
        changed.append(True)
        prop = api.props["metadata"]
        if change == "rename":
            prop["title"] = "renamed"
        elif change == "replace_id":
            prop["sheetId"] = 999999
        elif change in ("shrink", "grow"):
            prop["gridProperties"]["rowCount"] = 2 if change == "shrink" else 1000
        elif change == "columns":
            prop["gridProperties"]["columnCount"] += 1
        elif change == "sheet_type":
            prop["sheetType"] = "DATA_SOURCE"
        elif change == "header":
            api.rows["metadata"][0][0] = "changed"
        elif change == "journal":
            api.rows["run_status"].append(["another-writer"])
        else:
            api.props[title]["gridProperties"]["rowCount"] += 1

    api.write = modify_after_staging
    result = apply(datasets, api)
    assert result["status"] == "failed"
    assert result["verification"] == "structure_changed"
    assert not api.committed
    assert all(len(api.rows[name]) == 1 for name in datasets)
    assert not any(
        "copyPaste" in r or "updateCells" in r
        for kind, reqs in api.writes
        if kind == "batch"
        for r in reqs
    )


@pytest.mark.parametrize("change", ["delete", "shrink"])
def test_race_after_precommit_check_is_rejected_as_one_batch(datasets, api, change):
    original = api.batch
    before = deepcopy(api.rows)

    def race(requests):
        if any("copyPaste" in req for req in requests):
            if change == "delete":
                del api.props["metadata"]
            else:
                api.props["metadata"]["gridProperties"]["rowCount"] = 1
        return original(requests)

    api.batch = race
    result = apply(datasets, api)
    assert result["status"] == "partial"
    assert result["verification"] == "journal_mismatch"
    assert not api.committed
    for name in HEADERS:
        assert api.rows[name] == before[name]


@pytest.mark.parametrize("change", ["identity", "size", "journal"])
def test_race_after_final_batch_cannot_be_reported_as_verified_success(datasets, api, change):
    original = api.batch

    def change_after_commit(requests):
        response = original(requests)
        if any("copyPaste" in req for req in requests):
            if change == "identity":
                api.props["metadata"]["sheetId"] = 999999
            elif change == "size":
                api.props["metadata"]["gridProperties"]["rowCount"] += 1
            else:
                api.rows["run_status"].append(["concurrent-writer"])
        return response

    api.batch = change_after_commit
    result = apply(datasets, api)
    assert result["status"] == "partial"
    assert result["current_data_status"] == "unverified"
    assert result["verification"] == (
        "journal_mismatch" if change == "journal" else "structure_changed"
    )
    assert api.rows["run_status"][1][3] == "success"
    assert "staging_sheet_ids" in result


def test_change_during_data_verification_is_detected(datasets, api):
    original = api.read

    def change_after_read(title):
        rows = original(title)
        if api.committed and title == "validator_metrics":
            api.props["subnets"]["gridProperties"]["columnCount"] += 1
        return rows

    api.read = change_after_read
    result = apply(datasets, api)
    assert result["status"] == "partial"
    assert result["verification"] == "structure_changed"


def test_failure_journal_never_overwrites_concurrent_row(datasets, api):
    def change_journal_and_fail(title, rows):
        api.rows["run_status"].append(["other-writer"])
        raise SheetsError("Google API failure (403)")

    api.write = change_journal_and_fail
    result = apply(datasets, api)
    assert result["status"] == "failed" and result["journal_error"]
    assert api.rows["run_status"][1] == ["other-writer"]
    assert len(api.rows["run_status"]) == 2


def test_cli_cleanup_warning_and_historical_noop_have_success_exit(
    datasets, api, monkeypatch, capsys
):
    import json

    import taostats_pipeline.exporters.google_sheets as module

    api.fail = "cleanup"
    monkeypatch.setattr(module, "publish", lambda *a, **k: apply(datasets, api))
    assert main(["publish-sheets", "--input", str(FIXTURES), "--apply"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "success" and result["cleanup_warning"]
    assert main(["publish-sheets", "--input", str(FIXTURES), "--apply"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "already_published"


@pytest.mark.parametrize(
    "change", ["missing_record", "empty_record", "extra_record", "changed_text"]
)
def test_post_write_checks_detect_real_value_shape_mismatches(datasets, api, change):
    original = api.read

    def changed_response(title):
        rows = original(title)
        if api.committed and title == "metadata":
            if change == "missing_record":
                rows.pop()
            elif change == "empty_record":
                rows[-1] = []
            elif change == "extra_record":
                rows.extend([[], ["99", STAMP]])
            else:
                rows[-1][0] = "7.0"  # Numerically similar, but not our literal text.
        return rows

    api.read = changed_response
    result = apply(datasets, api)
    assert result["status"] == "partial" and result["verification"] == "mismatch"
    assert result["current_data_status"] == "differs"
    assert api.rows["run_status"][1][3] == "partial"


@pytest.mark.parametrize("change", ["removed", "replaced"])
def test_staging_change_after_commit_is_only_cleanup_warning(datasets, api, change):
    original = api.batch
    affected = []

    def change_after_commit(requests):
        response = original(requests)
        if any("copyPaste" in r for r in requests):
            title = next(title for title in api.props if title.startswith("_stage_"))
            affected.append(title)
            if change == "removed":
                del api.props[title]
                del api.rows[title]
            else:
                # Never delete a different sheet occupying the same title.
                api.props[title]["sheetId"] = 999999
        return response

    api.batch = change_after_commit
    result = apply(datasets, api)
    assert result["status"] == "success" and result["cleanup_warning"]
    assert api.rows["run_status"][1][3] == "success"
    assert not any("deleteSheet" in r for kind, reqs in api.writes if kind == "batch" for r in reqs)
    if change == "replaced":
        assert affected[0] in api.props


def test_journal_change_during_final_data_reads_is_not_overwritten(datasets, api):
    original = api.read

    def append_after_data_read(title):
        rows = original(title)
        if api.committed and title == "validator_metrics":
            api.rows["run_status"].append(["another-run"])
        return rows

    api.read = append_after_data_read
    result = apply(datasets, api)
    assert result["status"] == "partial"
    assert result["verification"] == "journal_mismatch"
    assert api.rows["run_status"][1][3] == "success"
    assert api.rows["run_status"][2] == ["another-run"]


def test_mismatch_does_not_downgrade_historical_success_after_concurrent_run(datasets, api):
    original = api.read
    changed = []

    def newer_run_during_read(title):
        rows = original(title)
        if api.committed and title == "subnets" and not changed:
            changed.append(True)
            rows[1][2] = "newer-run-data"
            api.rows["run_status"].append(["newer-run"])
        return rows

    api.read = newer_run_during_read
    result = apply(datasets, api)
    assert result["status"] == "partial"
    assert result["verification"] == "mismatch"
    assert result["journal_error"]
    assert api.rows["run_status"][1][3] == "success"
    assert api.rows["run_status"][2] == ["newer-run"]
