"""Load local fixtures, parse/normalize, validate, then optionally export."""

import json
from dataclasses import dataclass
from pathlib import Path

from .exporters import export
from .parsers import ParseError, parse_metadata, parse_metrics, parse_snapshots
from .validation import ValidationResult, validate


@dataclass
class PipelineResult:
    validation: ValidationResult
    generation: Path | None = None


def local_file(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(f"Fixture must be a file within input directory: {name!r}")
    return path


def run(input_dir: Path, output: Path | None = None) -> PipelineResult:
    datasets = {"subnets": [], "metadata": [], "validator_metrics": []}
    errors = []
    try:
        manifest = json.loads((input_dir / "manifest.json").read_text(encoding="utf-8"))
        expected = manifest["expected_ids"]
        if not isinstance(expected, list) or any(type(i) is not int or i < 0 for i in expected):
            raise ValueError("expected_ids must be a list of nonnegative integers")
        if len(set(expected)) != len(expected) or not expected:
            raise ValueError("expected_ids must be nonempty and unique")
        timestamp = manifest["collected_at_utc"]
        pages = manifest["subnets"]
        if not isinstance(pages, list):
            raise ValueError("subnets must be a list of manifest entries")

        def read(name):
            return local_file(input_dir, name).read_text(encoding="utf-8")

        try:
            datasets["subnets"] = parse_snapshots(read(manifest["snapshot"]), timestamp)
        except (OSError, ValueError, TypeError) as exc:
            errors.append(f"subnets: {exc}")
        for entry in pages:
            if not isinstance(entry, dict) or type(entry.get("netuid")) is not int:
                raise ValueError("Each subnet entry needs an integer netuid")
            for category in ("metadata", "validator_metrics"):
                try:
                    if category == "metadata":
                        record = parse_metadata(read(entry["metadata"]), timestamp)
                        if record.netuid != entry["netuid"]:
                            raise ParseError("Metadata netuid differs from manifest")
                    else:
                        metric_files = entry["metrics"]
                        if not isinstance(metric_files, list):
                            raise ValueError("metrics must be a list of page filenames")
                        record = parse_metrics(
                            [read(name) for name in metric_files], entry["netuid"], timestamp
                        )
                    datasets[category].append(record)
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    errors.append(f"{category} netuid={entry['netuid']}: {exc}")
        report = validate(datasets, set(expected))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report = validate(datasets)
        errors.append(f"Input manifest: {exc}")
    report.errors.extend(errors)
    report.status = "error" if report.errors else "ok"
    result = PipelineResult(report)
    if report.status == "ok" and output is not None:
        try:
            result.generation = export(datasets, output)
        except (OSError, ValueError, TypeError) as exc:
            report.status = "error"
            report.errors.append(f"Export: {exc}")
    return result
