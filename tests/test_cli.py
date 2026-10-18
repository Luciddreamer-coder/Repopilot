import contextlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.cli import main


class RepoPilotCliTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.database = self.root / "snapshots.sqlite3"
        (self.root / "main.py").write_text(
            "def main(value):\n"
            + "\n".join("    if value: return value" for _ in range(20))
            + "\n    return 0\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.directory.cleanup()

    def run_cli(self, *arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(("--database", str(self.database), *arguments))
        return code, output.getvalue()

    def test_scan_and_hotspots_emit_json(self):
        code, output = self.run_cli("scan", str(self.root))
        self.assertEqual(code, 0)
        report = json.loads(output)
        self.assertEqual(report["command"], "scan")
        self.assertEqual(report["summary"]["file_count"], 1)

        code, output = self.run_cli("hotspots", str(self.root))
        self.assertEqual(code, 0)
        hotspots = json.loads(output)
        self.assertEqual(hotspots["hotspots"][0]["path"], "main.py")

    def test_findings_and_report_formats(self):
        code, output = self.run_cli("findings", str(self.root))
        self.assertEqual(code, 0)
        findings = json.loads(output)
        self.assertIn("findings", findings)
        self.assertTrue(any(item["file"] == "main.py" for item in findings["findings"]))

        code, output = self.run_cli("report", str(self.root), "--format", "markdown")
        self.assertEqual(code, 0)
        self.assertIn("# RepoPilot report", output)
        self.assertIn("| File | Risk | Level |", output)
        self.assertIn("main.py", output)
        self.assertNotIn("src/api/routes.py", output)

        code, output = self.run_cli("report", str(self.root), "--format", "json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["command"], "report")

    def test_check_returns_nonzero_for_failed_gate_and_zero_for_pass(self):
        code, output = self.run_cli("check", str(self.root), "--max-risk", "0", "--max-new-findings", "0")
        self.assertEqual(code, 1)
        failed = json.loads(output)
        self.assertFalse(failed["passed"])
        self.assertTrue(failed["violations"])

        code, output = self.run_cli("check", str(self.root), "--max-risk", "100", "--max-new-findings", "0")
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output)["passed"])

    def test_omitted_database_uses_isolated_store(self):
        configured = self.root / "web.sqlite3"
        connection = sqlite3.connect(configured)
        try:
            connection.execute("CREATE TABLE marker(value TEXT)")
            connection.execute("INSERT INTO marker(value) VALUES ('untouched')")
            connection.commit()
        finally:
            connection.close()
        output = io.StringIO()
        old_value = os.environ.get("REPOPILOT_DATABASE_PATH")
        os.environ["REPOPILOT_DATABASE_PATH"] = str(configured)
        try:
            with contextlib.redirect_stdout(output):
                code = main(("scan", str(self.root)))
        finally:
            if old_value is None:
                os.environ.pop("REPOPILOT_DATABASE_PATH", None)
            else:
                os.environ["REPOPILOT_DATABASE_PATH"] = old_value
        self.assertEqual(code, 0)
        connection = sqlite3.connect(configured)
        try:
            self.assertEqual(connection.execute("SELECT value FROM marker").fetchone()[0], "untouched")
            self.assertFalse(connection.execute("SELECT name FROM sqlite_master WHERE name = 'snapshots'").fetchone())
        finally:
            connection.close()

    def test_structural_findings_are_included_in_cli_lifecycle(self):
        (self.root / "unresolved.py").write_text("import missing_package\n", encoding="utf-8")
        (self.root / "cycle_a.py").write_text("import cycle_b\n", encoding="utf-8")
        (self.root / "cycle_b.py").write_text("import cycle_a\n", encoding="utf-8")
        code, output = self.run_cli("findings", str(self.root))
        self.assertEqual(code, 0)
        findings = json.loads(output)["findings"]
        structural = {item["signal"]: item for item in findings if item["signal"] in {"unresolved_imports", "dependency_cycles"}}
        self.assertIn("unresolved_imports", structural)
        self.assertIn("dependency_cycles", structural)
        self.assertTrue(structural["unresolved_imports"]["evidence"])
        self.assertTrue(structural["dependency_cycles"]["recommendation"])

    def test_markdown_is_utf8_without_replacement_characters(self):
        (self.root / "café.py").write_text("def café():\n    return '✓'\n", encoding="utf-8")
        code, output = self.run_cli("report", str(self.root), "--format", "markdown")
        self.assertEqual(code, 0)
        self.assertNotIn("\ufffd", output)
        self.assertIn("café.py", output)


if __name__ == "__main__":
    unittest.main()
