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
        report = self.client.get("/dashboard").get_json()
        averages = {}
        for name in ("complexity", "change_frequency", "dependency_reach"):
            values = [signal["score"] for item in report["files"] for signal in item["signals"] if signal["name"] == name]
            averages[name] = round(sum(values) / len(values)) if values else 0
        self.assertIn(f"Risk radar: Complexity {averages['complexity']}".encode(), dashboard.data)
        self.assertIn(f"Change frequency {averages['change_frequency']}".encode(), dashboard.data)
        self.assertIn(f"Dependency reach {averages['dependency_reach']}".encode(), dashboard.data)
        self.assertIn(b"Dependency reach", dashboard.data)
        self.assertNotIn(b">Churn<", dashboard.data)
        self.assertIn(b"Dependency summary", dashboard.data)
        self.assertIn(b"src/api/routes.py", dashboard.data)
        self.assertNotIn(b"node-center", dashboard.data)
        self.assertIn(b"data-snapshot-id=\"snapshot-001\"", dashboard.data)
        self.assertIn(b"average risk", dashboard.data)

        detail = self.client.get("/file/src/api/routes.py", headers=headers)
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"Risk signals", detail.data)
        self.assertIn(b"Source preview", detail.data)

        missing = self.client.get("/file/missing.py", headers=headers)
        self.assertEqual(missing.status_code, 404)
        self.assertIn(b"File not found", missing.data)

    def test_chart_templates_expose_complete_data_for_accessible_interaction(self):
        headers = {"Accept": "text/html"}
        dashboard = self.client.get("/dashboard", headers=headers)
        self.assertIn(b"data-risk=\"", dashboard.data)

        self.client.post("/scan", json={"repo_path": str(FIXTURE)})
        dashboard = self.client.get("/dashboard", headers=headers)
        self.assertIn(b"data-label=\"low_coverage.py\"", dashboard.data)
        self.assertIn(b"aria-label=\"low_coverage.py: risk", dashboard.data)
        self.assertIn(b"title=\"low_coverage.py ", dashboard.data)
        self.assertIn(b"style=\"left:", dashboard.data)
        self.assertIn(b"data-coverage=\"10.0\"", dashboard.data)
        self.assertIn(b"radar-fill", dashboard.data)

        history = self.client.get("/history", headers=headers)
        self.assertEqual(history.status_code, 200)
        self.assertIn(b"snapshot-001", history.data)

    def test_crowded_trajectory_keeps_all_points_but_selects_labels(self):
        for _ in range(5):
            self.client.post("/scan", json={"repo_path": str(FIXTURE)})

        page = self.client.get("/dashboard", headers={"Accept": "text/html"})
        points = page.data.count(b"data-snapshot-id=")
        labels = page.data.count(b"<small>snapshot-")
        self.assertGreaterEqual(points, 7)
        self.assertLess(labels, points)
        self.assertIn(b"selected labels shown", page.data)

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

    def test_dependency_graph_has_deterministic_relationship_layout(self):
        self.client.post("/scan", json={"repo_path": str(FIXTURE)})

        first = self.client.get("/api/dependency-graph").get_json()
        second = self.client.get("/api/dependency-graph").get_json()
        self.assertEqual(first["layout"], second["layout"])
        self.assertEqual(first["nodes"], second["nodes"])
        self.assertEqual(first["edges"], second["edges"])

        self.assertEqual(first["layout"]["algorithm"], "deterministic-layered")
        self.assertGreater(first["layout"]["width"], 0)
        self.assertGreater(first["layout"]["height"], 0)
        nodes = {node["id"]: node for node in first["nodes"]}
        self.assertEqual(len({(node["x"], node["y"]) for node in nodes.values()}), len(nodes))
        self.assertTrue(all(node["width"] > 0 and node["height"] > 0 for node in nodes.values()))
        self.assertTrue(nodes["cycle_a.py"]["is_cycle"])
        self.assertTrue(nodes["unresolved_import.py"]["is_unresolved"])

        edges = {(edge["source"], edge["target"]): edge for edge in first["edges"]}
        cycle_edge = edges[("cycle_a.py", "cycle_b.py")]
        self.assertTrue(cycle_edge["is_cycle"])
        self.assertTrue(cycle_edge["path"].startswith("M "))
        self.assertEqual(cycle_edge["metadata"]["relationship"], "static_import")
        self.assertEqual(first["unresolved_imports"][0]["file"], "unresolved_import.py")

        page = self.client.get("/dependencies", headers={"Accept": "text/html"})
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"architecture-graph", page.data)
        self.assertIn(b"architecture-edge", page.data)
        self.assertIn(b"cycle_a.py", page.data)


if __name__ == "__main__":
    unittest.main()
