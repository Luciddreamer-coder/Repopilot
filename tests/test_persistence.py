import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import create_app
from app.persistence import SQLitePersistence


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
            selected = first_client.post(f"/api/snapshots/{baseline_id}/select")
            self.assertEqual(selected.status_code, 200)

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

    def test_legacy_database_is_migrated_in_place_and_versioned(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "legacy.sqlite3"
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE repositories (id INTEGER PRIMARY KEY, identity TEXT UNIQUE NOT NULL, name TEXT NOT NULL, path TEXT NOT NULL, branch TEXT NOT NULL, commit_hash TEXT NOT NULL, metadata_json TEXT NOT NULL);
                CREATE TABLE snapshots (id TEXT PRIMARY KEY, sequence INTEGER UNIQUE NOT NULL, repository_id INTEGER NOT NULL, generated_at TEXT NOT NULL, demo_mode INTEGER NOT NULL, weights_json TEXT NOT NULL);
                CREATE TABLE files (id INTEGER PRIMARY KEY, snapshot_id TEXT NOT NULL, path TEXT NOT NULL, language TEXT NOT NULL, lines INTEGER NOT NULL, functions INTEGER NOT NULL, risk_score INTEGER NOT NULL, risk_level TEXT NOT NULL, summary TEXT NOT NULL, content TEXT NOT NULL, UNIQUE(snapshot_id, path));
                CREATE TABLE findings (id INTEGER PRIMARY KEY, snapshot_id TEXT NOT NULL, file_path TEXT NOT NULL, name TEXT NOT NULL, score INTEGER NOT NULL, level TEXT NOT NULL, explanation TEXT NOT NULL, evidence_json TEXT NOT NULL);
                CREATE TABLE dependency_edges (snapshot_id TEXT NOT NULL, source_path TEXT NOT NULL, target_path TEXT NOT NULL, PRIMARY KEY(snapshot_id, source_path, target_path));
                CREATE TABLE app_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT INTO app_state(key, value) VALUES ('schema_version', '1');
                INSERT INTO repositories(id, identity, name, path, branch, commit_hash, metadata_json) VALUES (1, 'legacy', 'legacy', 'legacy', 'unknown', 'unknown', '{}');
                INSERT INTO snapshots(id, sequence, repository_id, generated_at, demo_mode, weights_json) VALUES ('legacy-snapshot', 1, 1, 'now', 0, '{}');
                INSERT INTO files(snapshot_id, path, language, lines, functions, risk_score, risk_level, summary, content) VALUES ('legacy-snapshot', 'main.py', 'Python', 1, 0, 1, 'low', 'legacy', 'pass');
                """
            )
            connection.commit()
            connection.close()

            persistence = SQLitePersistence(database)
            self.assertEqual(persistence.schema_version, 2)
            columns = {row[1] for row in persistence.connection.execute("PRAGMA table_info(files)")}
            self.assertTrue({"git_json", "metadata_json"}.issubset(columns))
            self.assertEqual(persistence.connection.execute("SELECT COUNT(*) FROM files").fetchone()[0], 1)
            persistence.close()


if __name__ == "__main__":
    unittest.main()
