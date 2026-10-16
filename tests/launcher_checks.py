"""Standalone offline launcher checks; run directly (outside pytest's process guard).

Uses only stdlib and PowerShell, no Pester, Python subprocess or Google client.
Each shell replaces py discovery with an inert .ps1 stub before running the launcher.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHELL = os.environ.get("LAUNCHER_TEST_SHELL") or shutil.which("pwsh") or shutil.which("powershell")


@unittest.skipUnless(SHELL, "PowerShell unavailable; run on Windows or install PowerShell 7")
class LauncherChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="launcher checks ")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.key = self.directory / "dummy key ключ.json"
        self.key.write_text("NOT A KEY; CONTENTS MUST NEVER BE READ", encoding="utf-8")
        self.config = self.directory / "local config.json"
        self.target = "synthetic_spreadsheet_12345"
        self.write_config()
        self.calls = self.directory / "calls.jsonl"
        self.stub = self.directory / "fake py.ps1"
        self.stub.write_text(
            """if ($args[0] -eq '--version') { exit ([int]$env:CHECK_VERSION_EXIT) }
[pscustomobject]@{
    arguments = @($args)
    target = $env:GOOGLE_SPREADSHEET_ID
    allowed = $env:GOOGLE_ALLOWED_SPREADSHEET_ID
    credentials = $env:GOOGLE_CREDENTIALS_FILE
} | ConvertTo-Json -Compress | Add-Content -LiteralPath $env:CHECK_CALLS -Encoding UTF8
exit ([int]$env:CHECK_EXIT)
""",
            encoding="utf-8",
        )

    def write_config(self, **updates):
        config = {"spreadsheet_id": self.target, "credentials_file": str(self.key)}
        config.update(updates)
        self.config.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")

    def launch(self, apply=False, run_id=None, config=True, answer="PUBLISH demo-001", **env):
        parameters = {}
        if apply:
            parameters["Apply"] = True
        if run_id is not None:
            parameters["RunId"] = run_id
        if config:
            parameters["ConfigPath"] = str(self.config)
        parameters.update(self.extra_parameters if hasattr(self, "extra_parameters") else {})
        params_file = self.directory / "parameters.json"
        params_file.write_text(json.dumps(parameters), encoding="utf-8")
        harness = self.directory / "harness.ps1"
        harness.write_text(
            """function Get-Command {
    param($Name, $CommandType, $ErrorAction)
    if ($env:CHECK_NO_PY -eq '1') { throw 'py missing' }
    [pscustomobject]@{ Source = $env:CHECK_STUB }
}
if ($env:CHECK_NO_CONFIRMATION -ne '1') {
    function Read-Host { param($Prompt) return $env:CHECK_ANSWER }
}
$parameters = @{}
$obj = Get-Content -LiteralPath $env:CHECK_PARAMS -Raw | ConvertFrom-Json
foreach ($p in $obj.PSObject.Properties) { $parameters[$p.Name] = $p.Value }
& $env:CHECK_LAUNCHER @parameters
$resultCode = $LASTEXITCODE
if ($env:GOOGLE_SPREADSHEET_ID -cne 'previous-target' -or
    $env:GOOGLE_CREDENTIALS_FILE -cne 'previous-credentials' -or
    $env:GOOGLE_ALLOWED_SPREADSHEET_ID -cne $env:CHECK_ORIGINAL_ALLOWED) {
    [Console]::Error.WriteLine('Environment was not restored')
    exit 99
}
exit $resultCode
""",
            encoding="utf-8",
        )
        environment = os.environ.copy()
        environment.update(
            GOOGLE_ALLOWED_SPREADSHEET_ID=self.target,
            GOOGLE_SPREADSHEET_ID="previous-target",
            GOOGLE_CREDENTIALS_FILE="previous-credentials",
            CHECK_STUB=str(self.stub),
            CHECK_CALLS=str(self.calls),
            CHECK_PARAMS=str(params_file),
            CHECK_LAUNCHER=str(ROOT / "scripts/publish_sheets.ps1"),
            CHECK_ANSWER=answer,
            CHECK_EXIT="0",
            CHECK_VERSION_EXIT="0",
            CHECK_NO_PY="0",
            CHECK_NO_CONFIRMATION="0",
        )
        environment.update(env)
        environment["CHECK_ORIGINAL_ALLOWED"] = environment["GOOGLE_ALLOWED_SPREADSHEET_ID"]
        result = subprocess.run(
            [
                SHELL,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(harness),
            ],
            env=environment,
            cwd=self.directory,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        calls = []
        if self.calls.exists():
            calls = [
                json.loads(line) for line in self.calls.read_text(encoding="utf-8-sig").splitlines()
            ]
        self.assertNotIn(self.key.read_text(), result.stdout + result.stderr)
        self.assertNotIn(self.target, result.stdout + result.stderr)
        self.assertNotIn("UnauthorizedAccess", result.stderr)
        return result, calls

    def assert_refused(self, **kwargs):
        result, calls = self.launch(apply=True, **kwargs)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(calls, [])
        return result

    def test_default_dry_run_without_configuration(self):
        self.config.unlink()
        result, calls = self.launch(config=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 1)
        arguments = calls[0]["arguments"]
        self.assertEqual(len(arguments), 6)
        self.assertEqual(arguments[:4], ["-m", "taostats_pipeline", "publish-sheets", "--input"])
        self.assertTrue(os.path.samefile(arguments[4], ROOT / "src/taostats_pipeline/fixtures"))
        self.assertEqual(arguments[5:], ["--dry-run"])
        self.assertEqual(calls[0]["target"], "previous-target")

    def test_dry_run_ignores_invalid_configuration(self):
        self.config.write_text("not JSON")
        result, calls = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--dry-run", calls[0]["arguments"])

    def test_allowlist_mismatch_or_missing(self):
        for allowed in ("another_synthetic_table_12345", "", self.target.upper()):
            with self.subTest(allowed=allowed):
                self.assert_refused(run_id="demo-001", GOOGLE_ALLOWED_SPREADSHEET_ID=allowed)

    def test_missing_key(self):
        self.write_config(credentials_file=str(self.directory / "absent.json"))
        self.assert_refused(run_id="demo-001")

    def test_run_id_required_and_validated(self):
        for run_id in (None, "", "bad id", "bad;id", "x" * 129, "demo\n"):
            with self.subTest(run_id=run_id):
                self.assert_refused(run_id=run_id)

    def test_confirmation_exact(self):
        for answer in ("Y", "", "publish demo-001", "PUBLISH demo-001 "):
            with self.subTest(answer=answer):
                self.assert_refused(run_id="demo-001", answer=answer)

    def test_noninteractive_confirmation_unavailable(self):
        # Keep real Read-Host in a -NonInteractive shell: no mock answer is possible.
        result = self.assert_refused(run_id="demo-001", CHECK_NO_CONFIRMATION="1")
        self.assertIn("Publication cancelled", result.stderr)

    def test_duplicate_json_properties(self):
        key = json.dumps(str(self.key))
        target = json.dumps(self.target)
        for document in (
            f'{{"spreadsheet_id":{target},"spreadsheet_id":{target},"credentials_file":{key}}}',
            f'{{"spreadsheet_id":{target},"credentials_file":{key},"credentials_file":{key}}}',
            f'{{"spreadsheet_id":{target},"spreadsheet_id":{target}}}',
            f'{{"spreadsheet_id":{target},"spreadsheet_\\u0069d":{target},"credentials_file":{key}}}',
            f'{{"spreadsheet_id":{target},"SPREADSHEET_ID":{target},"credentials_file":{key}}}',
        ):
            with self.subTest(document=document):
                self.config.write_text(document, encoding="utf-8")
                result = self.assert_refused(run_id="demo-001")
                self.assertIn("Invalid configuration", result.stderr)

    def test_config_wrong_types_and_empty_values(self):
        for field in ("spreadsheet_id", "credentials_file"):
            for value in (None, True, 123, [], {}, "", "   "):
                with self.subTest(field=field, value=value):
                    self.write_config(**{field: value})
                    result = self.assert_refused(run_id="demo-001")
                    self.assertIn("Invalid configuration", result.stderr)
        for document in ("null", "[]", '"text"', "{}", ""):
            with self.subTest(document=document):
                self.config.write_text(document, encoding="utf-8")
                self.assert_refused(run_id="demo-001")

    def test_utf8_config_with_and_without_bom(self):
        # Preserve the Cyrillic file identity, allowing Windows 8.3 path aliases.
        for encoding in ("utf-8", "utf-8-sig"):
            with self.subTest(encoding=encoding):
                try:
                    self.write_config()
                    document = self.config.read_text(encoding="utf-8")
                    self.config.write_text(document, encoding=encoding)
                    result, calls = self.launch(apply=True, run_id="demo-001")
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(len(calls), 1)
                    self.assertTrue(os.path.samefile(calls[0]["credentials"], self.key))
                finally:
                    # A failed assertion must not contaminate the next encoding's log.
                    self.calls.unlink(missing_ok=True)

    def test_apply_arguments_spaces_exit_and_no_retry(self):
        source = self.directory / "input with spaces & literal"
        source.mkdir()
        self.extra_parameters = {"InputPath": str(source)}
        result, calls = self.launch(apply=True, run_id="demo-001", CHECK_EXIT="23")
        self.assertEqual(result.returncode, 23, result.stdout + result.stderr)
        self.assertEqual(len(calls), 1)
        arguments = calls[0]["arguments"]
        # Exactly one argument after --input, including the spaces and literal &.
        self.assertEqual(len(arguments), 8)
        self.assertEqual(arguments[:4], ["-m", "taostats_pipeline", "publish-sheets", "--input"])
        self.assertTrue(os.path.samefile(arguments[4], source))
        self.assertIn(" ", arguments[4])
        self.assertIn("&", arguments[4])
        self.assertEqual(arguments[5:], ["--run-id", "demo-001", "--apply"])
        self.assertTrue(os.path.samefile(calls[0]["credentials"], self.key))
        self.assertEqual(calls[0]["target"], self.target)
        self.assertEqual(calls[0]["allowed"], self.target)

    def test_missing_python_or_runtime(self):
        self.assert_refused(run_id="demo-001", CHECK_NO_PY="1")
        self.assert_refused(run_id="demo-001", CHECK_VERSION_EXIT="1")

    def test_config_required_external_and_strict(self):
        self.assert_refused(run_id="demo-001", config=False)
        self.extra_parameters = {"ConfigPath": str(ROOT / "pyproject.toml")}
        self.assert_refused(run_id="demo-001")
        self.extra_parameters = {}
        self.write_config(allowed_spreadsheet_id=self.target)
        self.assert_refused(run_id="demo-001")
        self.config.write_text("malformed JSON")
        self.assert_refused(run_id="demo-001")

    def test_credentials_external_and_absolute(self):
        paths = ["relative.json", str(ROOT / "pyproject.toml")]
        if os.name == "nt":
            paths.extend(["C:relative.json", "\\root-relative.json"])
        for path in paths:
            with self.subTest(path=path):
                self.write_config(credentials_file=path)
                self.assert_refused(run_id="demo-001")

    def test_success_and_dry_run_failure_exit_codes(self):
        result, calls = self.launch(apply=True, run_id="demo-001")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 1)
        self.calls.unlink()
        result, calls = self.launch(CHECK_EXIT="17")
        self.assertEqual(result.returncode, 17, result.stderr)
        self.assertEqual(len(calls), 1)

    def test_linked_credentials_rejected(self):
        link = self.directory / "linked key.json"
        try:
            link.symlink_to(self.key)
        except OSError:
            self.skipTest("Creating symlinks requires local Windows privileges")
        self.write_config(credentials_file=str(link))
        self.assert_refused(run_id="demo-001")


if __name__ == "__main__":
    required = "--require-powershell" in sys.argv
    if required:
        sys.argv.remove("--require-powershell")
        if not SHELL or not Path(SHELL).is_file():
            sys.exit("Required PowerShell executable is unavailable")
    program = unittest.main(verbosity=2, exit=False)
    result = program.result
    all_skipped = result.testsRun == 0 or len(result.skipped) == result.testsRun
    if required and all_skipped:
        sys.exit("Required launcher suite ran no tests or skipped every test")
    sys.exit(0 if result.wasSuccessful() else 1)
