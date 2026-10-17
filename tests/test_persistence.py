import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import create_app


class RepoPilotPersistenceTests(unittest.TestCase):
    def test_scan_and_current_snapshot_survive_app_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "history.sqlite3"
            repository = Path(directory) / "repository"
            repository.mkdir()
            (repository / "main.py").write_text("def main():\n    return 1\n", encoding="utf-8")

            first = create_app({"DATABASE_PATH": str(database)})
            first_client = first.test_client()
            first_client.post("/scan", json={"repo_path": str(repository)})
            first_history = first_client.get("/api/history").get_json()
            current_id = first_history["current_snapshot_id"]
            self.assertEqual(first_history["snapshots"][-1]["id"], current_id)
            baseline_id = first_history["snapshots"][0]["id"]
            first.extensions["repopilot_store"].select_snapshot(baseline_id)

            reopened = create_app({"DATABASE_PATH": str(database)})
            reopened_client = reopened.test_client()
            report = reopened_client.get("/api/report").get_json()
            history = reopened_client.get("/api/history").get_json()
            self.assertEqual(report["repository"]["path"], "demo://repo-pilot")
            self.assertTrue(report["demo_mode"])
            self.assertEqual(history["current_snapshot_id"], baseline_id)
            self.assertIn(current_id, [item["id"] for item in history["snapshots"]])
            scanned_snapshot = reopened_client.get(f"/api/snapshots/{current_id}")
            self.assertEqual(scanned_snapshot.status_code, 200)
            self.assertEqual(scanned_snapshot.get_json()["repository"]["path"], str(repository))
            first.extensions["repopilot_store"].close()
            reopened.extensions["repopilot_store"].close()

    def test_schema_and_demo_seed_are_created_once(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "history.sqlite3"
            first = create_app({"DATABASE": str(database)})
            connection = sqlite3.connect(database)
            first_count = connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
            connection.close()
            self.assertEqual(first_count, 2)
            reopened = create_app({"DATABASE": str(database)})
            connection = sqlite3.connect(database)
            second_count = connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
            connection.close()
            self.assertEqual(second_count, first_count)
            connection = sqlite3.connect(database)
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            connection.close()
            self.assertTrue({"repositories", "snapshots", "files", "findings", "dependency_edges"}.issubset(tables))
            first.extensions["repopilot_store"].close()
            reopened.extensions["repopilot_store"].close()


if __name__ == "__main__":
    unittest.main()
