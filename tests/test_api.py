import tempfile
import unittest
from pathlib import Path

from app import create_app


class RepoPilotApiTests(unittest.TestCase):
    def setUp(self):
        self.client = create_app().test_client()

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


if __name__ == "__main__":
    unittest.main()
