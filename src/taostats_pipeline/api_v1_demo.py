"""Run the bundled, entirely synthetic metagraph demo and print JSON to stdout."""

import argparse
import json
import sys
from importlib.resources import files

from .api_v1 import Budget
from .api_v1_orchestration import analyze_metagraph, complete_metagraph

_MESSAGE = "Synthetic demo only; not real Bittensor data."


def _load_fixture():
    resource = files("taostats_pipeline").joinpath("fixtures").joinpath("metagraph-demo.json")
    return json.loads(resource.read_text(encoding="utf-8"))


def _report():
    fixture = _load_fixture()
    if type(fixture) is not dict or type(fixture.get("metadata")) is not dict:
        raise ValueError("invalid demo metadata")
    metadata = fixture["metadata"]
    if metadata.get("synthetic") is not True:
        raise ValueError("demo fixture must declare synthetic data")
    block = metadata["expected_block"]
    if type(block) is not int or block < 0:
        raise ValueError("demo fixture requires an explicit block")
    query = complete_metagraph(fixture["envelopes"], budget=Budget(2, 2))
    result = analyze_metagraph(query, netuid=metadata["netuid"], expected_block=block)
    return {
        "status": "ok",
        "mode": "offline",
        "synthetic": True,
        "message": _MESSAGE,
        "netuid": result.batch.netuid,
        "block": result.batch.block_number,
        "record_count": len(result.batch.records),
        "vtrust_avg": str(result.vtrust_avg) if result.vtrust_avg is not None else None,
        "vtrust_computed": result.vtrust_computed,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="taostats_pipeline.api_v1_demo", description=__doc__)
    arguments = list(sys.argv[1:] if argv is None else argv)
    report = {
        "status": "error",
        "mode": "offline",
        "synthetic": True,
        "message": "Synthetic offline demo could not be validated.",
    }
    if arguments and arguments not in (["--help"], ["-h"]):
        # Reject unknown arguments without echoing potential credentials or URLs.
        report["message"] = "Only --help is accepted; this demo has no runtime options."
        exit_code = 2
    else:
        parser.parse_args(arguments)
        try:
            report = _report()
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            exit_code = 1
        else:
            exit_code = 0
    print(json.dumps(report, ensure_ascii=True, allow_nan=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
