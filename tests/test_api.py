import tempfile
import unittest
from pathlib import Path

from app import create_app


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "experiment_repo"


class RepoPilotApiTests(unittest.TestCase):
    def setUp(self):
        self.database_directory = tempfile.TemporaryDirectory()
        self.app = create_app({"DATABASE_PATH": str(Path(self.database_directory.name) / "test.sqlite3")})
        self.client = self.app.test_client()

    def tearDown(self):
        self.app.extensions["repopilot_store"].close()
        self.database_directory.cleanup()

    def test_demo_dashboard_has_risk_signals(self):
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["demo_mode"])
        self.assertGreater(body["summary"]["file_count"], 0)
        self.assertIn("signals", body["files"][0])

    def test_scan_form_and_unknown_file(self):
        form = self.client.get("/scan")
        self.assertEqual(form.status_code, 200)
        self.assertEqual(form.get_json()["view"], "scan")
        missing = self.client.get("/file/does-not-exist.py")
        self.assertEqual(missing.status_code, 404)

    def test_scan_local_repository_and_read_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.py").write_text("def main():\n    if True:\n        return 1\n", encoding="utf-8")
            (root / "helper.py").write_text("def helper():\n    return 2\n", encoding="utf-8")
            response = self.client.post("/scan", json={"repo_path": directory})
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.get_json()["demo_mode"])
            detail = self.client.get("/file/main.py")
            self.assertEqual(detail.status_code, 200)
            self.assertEqual(detail.get_json()["file"]["language"], "Python")

    def test_report_and_compare(self):
        report = self.client.get("/api/report")
        self.assertEqual(report.status_code, 200)
        self.assertEqual(report.get_json()["view"], "report")
        compare = self.client.get("/compare?base=src/api/routes.py&target=src/models/report.py")
        self.assertEqual(compare.status_code, 200)
        self.assertIn("delta", compare.get_json())

    def test_browser_views_render_html(self):
        headers = {"Accept": "text/html"}
        dashboard = self.client.get("/dashboard", headers=headers)
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b"Signal radar", dashboard.data)
        self.assertIn(b"File risk overview", dashboard.data)

        detail = self.client.get("/file/src/api/routes.py", headers=headers)
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"Risk signals", detail.data)
        self.assertIn(b"Source preview", detail.data)

        missing = self.client.get("/file/missing.py", headers=headers)
        self.assertEqual(missing.status_code, 404)
        self.assertIn(b"File not found", missing.data)

    def test_intelligence_endpoints(self):
        history = self.client.get("/api/history")
        self.assertEqual(history.status_code, 200)
        self.assertGreaterEqual(len(history.get_json()["snapshots"]), 2)

        findings = self.client.get("/api/findings")
        self.assertGreater(findings.get_json()["count"], 0)

        impact = self.client.get("/api/change-impact?file=src/api/routes.py")
        self.assertEqual(impact.status_code, 200)
        self.assertGreaterEqual(impact.get_json()["affected_count"], 1)

        graph = self.client.get("/api/dependency-graph")
        self.assertEqual(len(graph.get_json()["nodes"]), 5)

        git_activity = self.client.get("/api/git/activity")
        self.assertEqual(git_activity.status_code, 200)
        self.assertEqual(len(git_activity.get_json()["files"]), 5)

        finding_compare = self.client.get("/api/findings/compare")
        self.assertEqual(finding_compare.status_code, 200)
        self.assertIn("counts", finding_compare.get_json())
        self.assertGreaterEqual(finding_compare.get_json()["counts"]["improved"], 1)

        weights = self.client.post("/api/scoring/weights", json={"complexity": 60, "change_frequency": 20, "dependency_reach": 20})
        self.assertEqual(weights.status_code, 200)
        self.assertEqual(weights.get_json()["weights"]["complexity"], 0.6)

        markdown = self.client.get("/api/report/markdown")
        self.assertEqual(markdown.status_code, 200)
        self.assertIn("text/markdown", markdown.content_type)
        self.assertIn(b"# RepoPilot report", markdown.data)

    def test_file_detail_and_impact_expose_evidence_context(self):
        self.client.post("/scan", json={"repo_path": str(FIXTURE)})

        detail = self.client.get("/api/files/unresolved_import.py").get_json()
        file_data = detail["file"]
        self.assertEqual(detail["view"], "file_detail")
        self.assertIn("git", file_data)
        self.assertEqual(file_data["coverage_state"], "not_detected")
        self.assertIn("missing_local_package", file_data["unresolved_imports"])
        self.assertTrue(detail["findings"])
        self.assertTrue(detail["structural_findings"])

        impact = self.client.get("/api/change-impact?file=cycle_a.py").get_json()
        self.assertIn("direct_risk", impact)
        self.assertIn("direct_dependency_risk", impact)
        self.assertIn("affected_risk", impact)
        self.assertTrue(impact["direct_dependency_details"])
        self.assertTrue(impact["affected_file_details"])
        self.assertTrue(impact["evidence"])
        self.assertTrue(any(item["type"] == "dependency_cycles" for item in impact["warnings"]))
        self.assertIn("git", impact)
        self.assertIn("repository_git", impact)

        detail_page = self.client.get("/file/unresolved_import.py", headers={"Accept": "text/html"})
        self.assertIn(b"Git activity", detail_page.data)
        self.assertIn(b"Unresolved imports", detail_page.data)
        self.assertIn(b"Structural warnings", detail_page.data)
        impact_page = self.client.get("/impact?file=cycle_a.py", headers={"Accept": "text/html"})
        self.assertIn(b"Risk evidence", impact_page.data)
        self.assertIn(b"Warnings", impact_page.data)
        self.assertIn(b"Recent activity", impact_page.data)


if __name__ == "__main__":
    unittest.main()
