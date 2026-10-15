"""Publish a complete generation through one atomic manifest replacement."""

import csv
import json
import os
import shutil
from dataclasses import asdict, fields
from decimal import Decimal
from pathlib import Path
from tempfile import mkdtemp
from uuid import uuid4

from ..validation import SCHEMAS, validate


def serialize(value):
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def write_dataset(directory: Path, name: str, records: list) -> None:
    rows = [asdict(record) for record in records]
    columns = [column.name for column in fields(SCHEMAS[name])]
    with (directory / f"{name}.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    with (directory / f"{name}.json").open("w", encoding="utf-8") as handle:
        json.dump(rows, handle, default=serialize, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def export(datasets: dict, output: Path, *, strict: bool = False) -> Path:
    report = validate(datasets, strict=strict)
    if report.status != "ok":
        raise ValueError("Export refused: " + "; ".join(report.errors))
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    staging = Path(mkdtemp(prefix=".staging-", dir=output))
    generation = output / f"run-{uuid4().hex}"
    pointer = output / f".current-{uuid4().hex}.tmp"
    publication_attempted = False
    try:
        for name, records in datasets.items():
            write_dataset(staging, name, records)
        os.replace(staging, generation)
        with pointer.open("w", encoding="utf-8") as handle:
            json.dump({"generation": generation.name, "datasets": list(SCHEMAS)}, handle, indent=2)
            handle.write("\n")
        # An exception can arrive after replace commits but before it returns.
        # From this point onward the complete generation must never be removed.
        publication_attempted = True
        os.replace(pointer, output / "current.json")
        return generation
    finally:
        if staging.exists():
            shutil.rmtree(staging)
        pointer.unlink(missing_ok=True)
        if not publication_attempted and generation.exists():
            shutil.rmtree(generation)
