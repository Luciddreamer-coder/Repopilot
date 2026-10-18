import json
import tempfile
import unittest
from pathlib import Path

from app import create_app
from app.scanner import scan_repository
from experiments.run_experiments import run_experiments


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "experiment_repo"


class RepoPilotExperimentTests(unittest.TestCase):
    def test_fixture_markers_are_detected(self):
        report = scan_repository(FIXTURE)
        files = {item.path: item for item in report.files}
        self.assertIn("complex_hotspot.py", files)
        self.assertIn("dependency_hub.py", files)
        self.assertIn("healthy.py", files)
        self.assertIn("refactor_candidate.py", files)
        self.assertIn("missing_local_package", files["unresolved_import.py"].unresolved_imports)
        self.assertIn("third_party_missing", files["unresolved_import.py"].unresolved_imports)
        self.assertEqual(report.dependency_cycles, [["cycle_a.py", "cycle_b.py"]])
        self.assertEqual(files["low_coverage.py"].coverage_source, "coverage.xml")
        self.assertIn("low_coverage", {signal.name for signal in files["low_coverage.py"].signals})

    def test_runner_writes_all_experiment_sections_and_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "evidence"
            results = run_experiments(FIXTURE, output)
            self.assertEqual(set(results), {"fixture", "A", "B", "C", "D"})
            self.assertEqual(results["A"]["precision_at_5"], 0.8)
            self.assertEqual(results["A"]["false_positives"], ["cycle_a.py"])
            self.assertTrue(results["B"]["improved"])
            self.assertTrue(results["B"]["fixture_unchanged"])
            self.assertTrue(results["C"]["matches"])
            self.assertEqual(len(results["D"]["configurations"]), 4)

            json_path = output / "experiments.json"
            markdown_path = output / "experiments.md"
            self.assertTrue(json_path.is_file())
            self.assertTrue(markdown_path.is_file())
            saved = json.loads(json_path.read_text(encoding="utf-8"))
            evidence = markdown_path.read_text(encoding="utf-8")
            self.assertEqual(saved["A"]["precision_at_5"], results["A"]["precision_at_5"])
            for heading in ("Experiment A", "Experiment B", "Experiment C", "Experiment D"):
                self.assertIn(heading, evidence)

    def test_evidence_center_route_and_downloads(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({"DATABASE_PATH": str(Path(directory) / "app.sqlite3")})
            client = app.test_client()

            api_response = client.get("/api/experiments")
            self.assertEqual(api_response.status_code, 200)
            self.assertTrue(api_response.get_json()["available"])
            self.assertIn("A", api_response.get_json())

            page = client.get("/experiments", headers={"Accept": "text/html"})
            self.assertEqual(page.status_code, 200)
            self.assertIn(b"Evidence you can", page.data)
            self.assertIn(b"Hotspot precision@5", page.data)

            json_download = client.get("/reports/experiments/experiments.json")
            markdown_download = client.get("/reports/experiments/experiments.md")
            json_download.get_data()
            markdown_download.get_data()
            self.assertEqual(json_download.status_code, 200)
            self.assertEqual(markdown_download.status_code, 200)
            self.assertIn("application/json", json_download.content_type)
            self.assertIn("text/markdown", markdown_download.content_type)
            json_download.close()
            markdown_download.close()
            app.extensions["repopilot_store"].close()

    def test_coverage_visuals_appear_after_scanning_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({"DATABASE_PATH": str(Path(directory) / "coverage.sqlite3")})
            client = app.test_client()
            client.post("/scan", json={"repo_path": str(FIXTURE)})
            dashboard = client.get("/dashboard", headers={"Accept": "text/html"})
            detail = client.get("/file/low_coverage.py", headers={"Accept": "text/html"})
            self.assertIn(b"Risk vs coverage", dashboard.data)
            self.assertIn(b"coverage-scatter", dashboard.data)
            self.assertIn(b"coverage-detail-card", detail.data)
            self.assertIn(b"coverage.xml", detail.data)
            app.extensions["repopilot_store"].close()

    def test_structural_findings_have_lifecycle_evidence_and_recommendations(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({"DATABASE_PATH": str(Path(directory) / "findings.sqlite3")})
            client = app.test_client()
            client.post("/scan", json={"repo_path": str(FIXTURE)})
            body = client.get("/api/findings").get_json()
            structural = [item for item in body["findings"] if item["signal"] in {"unresolved_imports", "dependency_cycles"}]
            self.assertTrue(structural)
            self.assertTrue(all(item["evidence"] for item in structural))
            self.assertTrue(all(item["recommendation"] for item in structural))
            self.assertTrue(all(item["status"] in {"new", "open", "regressed", "improved"} for item in structural))
            graph = client.get("/api/dependency-graph").get_json()
            self.assertTrue(graph["unresolved_imports"])
            self.assertEqual(graph["cycles"], [["cycle_a.py", "cycle_b.py"]])
            app.extensions["repopilot_store"].close()

    def test_experiment_check_mode_passes_controlled_invariants(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "evidence"
            results = run_experiments(FIXTURE, output)
            from experiments.run_experiments import _check_invariants

            self.assertEqual(_check_invariants(results), [])


if __name__ == "__main__":
    unittest.main()
