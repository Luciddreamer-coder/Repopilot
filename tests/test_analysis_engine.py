import tempfile
import unittest
from pathlib import Path

from app import create_app
from app.scanner import scan_repository


class RepoPilotAnalysisEngineTests(unittest.TestCase):
    def test_cobertura_and_lcov_coverage_are_attached_to_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.py").write_text("def main():\n    return 1\n", encoding="utf-8")
            (root / "coverage.xml").write_text(
                """<coverage><packages><package name=""><classes>
                <class name="main" filename="main.py"><lines>
                <line number="1" hits="1"/><line number="2" hits="0"/>
                </lines></class></classes></package></packages></coverage>""",
                encoding="utf-8",
            )
            report = scan_repository(root)
            item = report.files[0]
            self.assertEqual(item.coverage_percent, 50.0)
            self.assertEqual(item.coverage_covered_lines, 1)
            self.assertEqual(item.coverage_missing_lines, 1)
            self.assertEqual(item.coverage_source, "coverage.xml")
            self.assertIn("low_coverage", {signal.name for signal in item.signals})
            self.assertEqual(item.to_dict()["coverage"]["total_lines"], 2)

            (root / "coverage.xml").unlink()
            (root / "lcov.info").write_text(
                "SF:main.py\nDA:1,1\nDA:2,0\nLF:2\nLH:1\nend_of_record\n",
                encoding="utf-8",
            )
            lcov_item = scan_repository(root).files[0]
            self.assertEqual(lcov_item.coverage_percent, 50.0)
            self.assertEqual(lcov_item.coverage_source, "lcov.info")

    def test_unresolved_imports_do_not_create_fake_dependency_edges(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pkg").mkdir()
            (root / "pkg" / "main.py").write_text(
                "from .helper import value\nfrom .missing import nope\nimport third_party\n",
                encoding="utf-8",
            )
            (root / "pkg" / "helper.py").write_text("value = 1\n", encoding="utf-8")
            report = scan_repository(root)
            item = next(item for item in report.files if item.path == "pkg/main.py")
            self.assertEqual(item.dependencies, ["pkg/helper.py"])
            self.assertIn(".missing", item.unresolved_imports)
            self.assertIn("third_party", item.unresolved_imports)
            graph = report.to_dict()["dependency_graph"]
            self.assertEqual(graph["unresolved_imports"]["pkg/main.py"], item.unresolved_imports)
            self.assertEqual([(edge["source"], edge["target"]) for edge in (
                {"source": item.path, "target": target} for target in item.dependencies
            )], [("pkg/main.py", "pkg/helper.py")])

    def test_relative_imports_and_circular_dependencies_are_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pkg").mkdir()
            (root / "pkg" / "a.py").write_text("from . import b\n", encoding="utf-8")
            (root / "pkg" / "b.py").write_text("from .a import value\n", encoding="utf-8")
            report = scan_repository(root)
            a = next(item for item in report.files if item.path == "pkg/a.py")
            b = next(item for item in report.files if item.path == "pkg/b.py")
            self.assertEqual(a.dependencies, ["pkg/b.py"])
            self.assertEqual(b.dependencies, ["pkg/a.py"])
            self.assertEqual(report.dependency_cycles, [["pkg/a.py", "pkg/b.py"]])
            self.assertEqual(a.dependency_cycles, report.dependency_cycles)
            self.assertEqual(report.to_dict()["dependency_graph"]["cycles"], report.dependency_cycles)

    def test_coverage_and_boundary_metadata_survive_snapshot_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            (root / "main.py").write_text("import missing\n", encoding="utf-8")
            (root / "coverage.xml").write_text(
                '<coverage><class filename="main.py"><lines><line number="1" hits="0"/></lines></class></coverage>',
                encoding="utf-8",
            )
            database = Path(directory) / "snapshots.sqlite3"
            app = create_app({"DATABASE_PATH": str(database)})
            app.test_client().post("/scan", json={"repo_path": str(root)})
            app.extensions["repopilot_store"].close()

            reopened = create_app({"DATABASE_PATH": str(database)})
            item = next(item for item in reopened.test_client().get("/api/report").get_json()["files"] if item["path"] == "main.py")
            self.assertEqual(item["coverage_percent"], 0.0)
            self.assertEqual(item["coverage_source"], "coverage.xml")
            self.assertEqual(item["unresolved_imports"], ["missing"])
            reopened.extensions["repopilot_store"].close()


if __name__ == "__main__":
    unittest.main()
