"""Load only pure legacy functions, without executing module-level code."""

import ast
import re
import socket
import subprocess
from pathlib import Path
from statistics import mean
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
FUNCTIONS = {
    "main.py": {"clean", "parse_table"},
    "subnets_full_parser.py": {
        "get_max_subnets",
        "clean_concat_texts",
        "clean_bittensor",
        "clean_emission",
        "clean_price",
        "parse_metagraph",
    },
    "subnets_vtrust_incentive.py": {"get_max_subnets", "parse_table_for_metrics"},
}


@pytest.fixture(autouse=True)
def block_external_operations(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Unit tests must not use network or child processes")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(socket.socket, "sendto", blocked)
    monkeypatch.setattr(subprocess, "Popen", blocked)


@pytest.fixture
def parsers():
    modules = {}
    for filename, names in FUNCTIONS.items():
        source = ROOT / "src" / filename
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        selected = [
            node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names
        ]
        assert {node.name for node in selected} == names
        # Fail closed if future definitions add executable decorators/defaults.
        for node in selected:
            assert not node.decorator_list
            assert not node.args.defaults and not any(node.args.kw_defaults)
            assert node.returns is None
            assert all(arg.annotation is None for arg in node.args.args)
        namespace = {"BeautifulSoup": BeautifulSoup, "re": re, "mean": mean}
        code = compile(ast.Module(body=selected, type_ignores=[]), str(source), "exec")
        exec(code, namespace)
        modules[source.stem] = SimpleNamespace(**{name: namespace[name] for name in names})
    return SimpleNamespace(**modules)


@pytest.fixture
def html_fixture():
    def read(name):
        return (ROOT / "tests" / "fixtures" / name).read_text(encoding="utf-8")

    return read
