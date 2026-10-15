"""Opt-in Sheets publication. No Google imports or I/O during offline dry-run."""

import hashlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from ..validation import SCHEMAS, validate

STATUS_COLUMNS = [
    "run_id",
    "started_at_utc",
    "finished_at_utc",
    "status",
    "subnets_count",
    "metadata_count",
    "validator_metrics_count",
    "error_summary",
    "data_sha256",
]
HEADERS = {name: [f.name for f in fields(model)] for name, model in SCHEMAS.items()}
HEADERS["run_status"] = STATUS_COLUMNS
SCOPE = "https://www.googleapis.com/auth/spreadsheets"


class SheetsError(Exception):
    """Safe, deliberately redacted diagnostic."""


@dataclass(frozen=True)
class Config:
    spreadsheet_id: str
    allowed_id: str
    credentials_file: str

    @classmethod
    def from_env(cls):
        return cls(
            *(
                os.environ.get(key, "")
                for key in (
                    "GOOGLE_SPREADSHEET_ID",
                    "GOOGLE_ALLOWED_SPREADSHEET_ID",
                    "GOOGLE_CREDENTIALS_FILE",
                )
            )
        )

    def check(self):
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,}", self.spreadsheet_id):
            raise SheetsError("Invalid GOOGLE_SPREADSHEET_ID")
        if not self.allowed_id or self.spreadsheet_id != self.allowed_id:
            raise SheetsError("Spreadsheet allowlist mismatch")


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def cell(value):
    # Explicit strings keep Decimal precision, timestamps and formula-like text literal.
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    if type(value) is int or isinstance(value, str):
        return str(value)
    raise SheetsError("Unsupported cell type")


def tables(datasets, strict=False):
    if validate(datasets, strict=strict).status != "ok":
        raise SheetsError("Input validation failed")
    for name, model in SCHEMAS.items():
        for record in datasets[name]:
            for column in fields(model):
                value = getattr(record, column.name)
                if "str" in str(column.type) and value is not None and not isinstance(value, str):
                    raise SheetsError("Input validation failed: expected text field")
    return {
        name: [HEADERS[name]]
        + [
            [cell(value) for value in asdict(record).values()]
            for record in sorted(datasets[name], key=lambda record: record.netuid)
        ]
        for name in SCHEMAS
    }


def normalized(rows, width):
    result = [list(map(str, row)) + [""] * (width - len(row)) for row in rows]
    while result and not any(result[-1]):
        result.pop()
    return result


class GoogleAPI:
    """Small injectable API boundary; only idempotent operations are retried."""

    def __init__(self, config, sleeper=time.sleep):
        config.check()
        self.config, self.sleeper = config, sleeper
        if not config.credentials_file or not Path(config.credentials_file).is_file():
            raise SheetsError("GOOGLE_CREDENTIALS_FILE is missing or unavailable")
        try:
            from google.oauth2.service_account import Credentials
            from googleapiclient.discovery import build

            credentials = Credentials.from_service_account_file(
                config.credentials_file, scopes=[SCOPE]
            )
            self.api = build(
                "sheets",
                "v4",
                credentials=credentials,
                cache_discovery=False,
                static_discovery=True,
            ).spreadsheets()
        except Exception:
            raise SheetsError(
                "Google client initialization failed; check credentials and extra"
            ) from None

    def execute(self, factory, *, retry=False, write=False):
        for attempt in range(3):
            if write:
                self.config.check()
            try:
                return factory().execute(num_retries=0)
            except Exception as exc:
                status = getattr(getattr(exc, "resp", None), "status", None)
                transient = status in (429, 500, 502, 503, 504) or isinstance(
                    exc, (TimeoutError, ConnectionError, OSError)
                )
                if retry and transient and attempt < 2:
                    self.sleeper(0.25 * 2**attempt)
                    continue
                # Never expose API bodies, credential paths or exception text.
                code = str(status) if type(status) is int else "transport/client"
                raise SheetsError(f"Google API failure ({code})") from None

    def structure(self):
        return self.execute(
            lambda: self.api.get(
                spreadsheetId=self.config.spreadsheet_id,
                fields="sheets.properties(sheetId,title,gridProperties)",
            ),
            retry=True,
        )

    def read(self, title):
        return self.execute(
            lambda: self.api.values().get(
                spreadsheetId=self.config.spreadsheet_id,
                range=f"'{title}'",
                valueRenderOption="UNFORMATTED_VALUE",
            ),
            retry=True,
        ).get("values", [])

    def batch(self, requests):
        return self.execute(
            lambda: self.api.batchUpdate(
                spreadsheetId=self.config.spreadsheet_id, body={"requests": requests}
            ),
            write=True,
        )

    def write(self, title, rows):
        return self.execute(
            lambda: self.api.values().update(
                spreadsheetId=self.config.spreadsheet_id,
                range=f"'{title}'!A1",
                valueInputOption="RAW",
                body={"values": rows},
            ),
            retry=True,
            write=True,
        )


def update_rows(sheet_id, rows, start=0, end=None):
    width = len(rows[0])
    area = {
        "sheetId": sheet_id,
        "startRowIndex": start,
        "endRowIndex": end or start + len(rows),
        "startColumnIndex": 0,
        "endColumnIndex": width,
    }
    return {
        "updateCells": {
            "range": area,
            "fields": "userEnteredValue",
            "rows": [
                {"values": [{"userEnteredValue": {"stringValue": cell(v)}} for v in row]}
                for row in rows
            ],
        }
    }


def publish(
    datasets, *, apply=False, config=None, api_factory=GoogleAPI, run_id=None, strict=False
):
    run_id = uuid4().hex if run_id is None else run_id
    result = {
        "run_id": run_id,
        "status": "failed",
        "error_summary": "",
        "record_counts": {},
        "dry_run": not apply,
        "already_published": False,
    }
    started = now()
    api = None
    stage_ids = []
    journal_ready = False
    commit_attempted = False
    journal_written = False
    try:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", run_id):
            raise SheetsError("Invalid run_id")
        prepared = tables(datasets, strict)
        result["record_counts"] = {name: len(rows) - 1 for name, rows in prepared.items()}
        digest = hashlib.sha256(
            json.dumps(prepared, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
        if not apply:
            result["status"] = "dry_run"
            result["remote_structure_checked"] = False
            return result
        config = config or Config.from_env()
        config.check()
        api = api_factory(config)
        properties = {s["properties"]["title"]: s["properties"] for s in api.structure()["sheets"]}
        if not set(HEADERS).issubset(properties):
            raise SheetsError("Required sheet names missing (exact names required)")
        existing = {}
        for name, header in HEADERS.items():
            existing[name] = api.read(name)
            if not existing[name] or existing[name][0] != header:
                raise SheetsError(f"Header mismatch: {name}")
            if any(len(row) > len(header) for row in existing[name]):
                raise SheetsError(f"Unexpected extra columns: {name}")
        journal = existing["run_status"]
        matches = [row for row in journal[1:] if row and row[0] == run_id]
        if matches:
            if (
                len(matches) == 1
                and len(matches[0]) == len(STATUS_COLUMNS)
                and (matches[0][3] == "success" and matches[0][-1] == digest)
            ):
                result.update(status="success", already_published=True)
                return result
            raise SheetsError("run_id already exists with different data or non-success status")
        row_index = len(journal)
        status_id = properties["run_status"]["sheetId"]
        if properties["run_status"]["gridProperties"]["rowCount"] <= row_index:
            raise SheetsError("run_status grid is full; expand it manually")
        journal_ready = True

        def status_row(status, error=""):
            return [
                run_id,
                started,
                now(),
                status,
                *result["record_counts"].values(),
                error,
                digest,
            ]

        stages = {}
        for name, rows in prepared.items():
            title = f"_stage_{uuid4().hex}_{name}"
            sheet_id = int(uuid4().hex[:7], 16)
            stages[name] = (title, sheet_id)
            # Remember before request: a lost response can still leave a staging sheet.
            stage_ids.append(sheet_id)
            config.check()
            api.batch(
                [
                    {
                        "addSheet": {
                            "properties": {
                                "sheetId": sheet_id,
                                "title": title,
                                "hidden": True,
                                "gridProperties": {
                                    "rowCount": len(rows),
                                    "columnCount": len(rows[0]),
                                },
                            }
                        }
                    }
                ]
            )
            config.check()
            api.write(title, rows)
            if normalized(api.read(title), len(rows[0])) != rows:
                raise SheetsError("Staging verification failed")
        requests = []
        for name, rows in prepared.items():
            prop = properties[name]
            height = max(prop["gridProperties"]["rowCount"], len(rows))
            width = max(prop["gridProperties"]["columnCount"], len(rows[0]))
            requests.append(
                {
                    "updateSheetProperties": {
                        "properties": {
                            "sheetId": prop["sheetId"],
                            "gridProperties": {"rowCount": height, "columnCount": width},
                        },
                        "fields": "gridProperties.rowCount,gridProperties.columnCount",
                    }
                }
            )
            # Clear obsolete values only inside the same atomic batch as copying the new data.
            requests.append(
                {
                    "updateCells": {
                        "range": {"sheetId": prop["sheetId"]},
                        "fields": "userEnteredValue",
                    }
                }
            )
            requests.append(
                {
                    "copyPaste": {
                        "source": {
                            "sheetId": stages[name][1],
                            "startRowIndex": 0,
                            "endRowIndex": len(rows),
                            "startColumnIndex": 0,
                            "endColumnIndex": len(rows[0]),
                        },
                        "destination": {
                            "sheetId": prop["sheetId"],
                            "startRowIndex": 0,
                            "endRowIndex": len(rows),
                            "startColumnIndex": 0,
                            "endColumnIndex": len(rows[0]),
                        },
                        "pasteType": "PASTE_VALUES",
                    }
                }
            )
        committed_row = status_row("success")
        requests.append(update_rows(status_id, [committed_row], row_index))
        config.check()
        commit_attempted = True
        try:
            api.batch(requests)
        except SheetsError:
            # No blind retries: the server may have committed before losing the response.
            pass
        observed = api.read("run_status")
        if len(observed) <= row_index or observed[row_index] != list(map(cell, committed_row)):
            raise SheetsError("Final publication outcome unresolved")
        journal_written = True
        for name, rows in prepared.items():
            if normalized(api.read(name), len(rows[0])) != rows:
                raise SheetsError("Published data verification failed")
        result["status"] = "success"
        # Cleanup is an operation required for a clean success report.
        config.check()
        api.batch([{"deleteSheet": {"sheetId": i}} for i in stage_ids])
        stage_ids.clear()
    except Exception as exc:
        error = str(exc) if isinstance(exc, SheetsError) else "Sheets publication failed"
        result.update(status="partial" if commit_attempted else "failed", error_summary=error)
        if api and journal_ready and (not commit_attempted or journal_written):
            try:
                config.check()
                api.batch(
                    [update_rows(status_id, [status_row(result["status"], error)], row_index)]
                )
            except Exception:
                result["journal_error"] = "Run status could not be recorded"
        # Unknown commit outcome must never overwrite a potentially successful journal row.
    if stage_ids:
        result["staging_sheet_ids"] = stage_ids
    return result
