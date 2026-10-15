"""Offline commands; JSON reports are suitable for local automation."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .pipeline import run


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="Export bundled synthetic fixtures")
    demo.add_argument("--output", type=Path, default=Path("output"))
    local = commands.add_parser("run", help="Process local HTML and manifest")
    local.add_argument("--input", type=Path, required=True)
    local.add_argument("--output", type=Path, required=True)
    check = commands.add_parser("validate", help="Validate local HTML without exporting")
    check.add_argument("--input", type=Path, required=True)
    sheets = commands.add_parser("publish-sheets", help="Validate locally; opt in to Sheets writes")
    sheets.add_argument("--input", type=Path, required=True)
    mode = sheets.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    sheets.add_argument("--run-id", help="Stable publication identifier for deduplication")
    for command in (demo, local, check, sheets):
        command.add_argument(
            "--strict",
            action=argparse.BooleanOptionalAction,
            default=None,
            help="Require critical metrics; overrides manifest strict_validation",
        )
    args = parser.parse_args(argv)
    source = Path(__file__).parent / "fixtures" if args.command == "demo" else args.input
    result = run(source, getattr(args, "output", None), strict=args.strict)
    if args.command == "publish-sheets":
        from .exporters.google_sheets import publish

        if result.validation.status != "ok":
            print(
                json.dumps(
                    {
                        "status": "failed",
                        "error_summary": "Input validation failed",
                        "validation": asdict(result.validation),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
        report = publish(
            result.datasets,
            apply=args.apply,
            run_id=args.run_id,
            strict=args.strict if args.strict is not None else False,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["status"] in ("success", "dry_run") else 1
    report = asdict(result.validation)
    report["generation"] = str(result.generation) if result.generation else None
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if result.validation.status == "ok" else 1
