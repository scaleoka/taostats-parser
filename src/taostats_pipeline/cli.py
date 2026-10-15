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
    args = parser.parse_args(argv)
    source = Path(__file__).parent / "fixtures" if args.command == "demo" else args.input
    result = run(source, getattr(args, "output", None))
    report = asdict(result.validation)
    report["generation"] = str(result.generation) if result.generation else None
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if result.validation.status == "ok" else 1
