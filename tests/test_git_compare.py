import subprocess
import tempfile
import unittest
from pathlib import Path

from app import create_app
from app.scanner import git_compare, scan_repository


class RepoPilotGitComparisonTests(unittest.TestCase):
    def test_git_compare_reports_refs_status_and_numstat(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._git(root, "init", "-q")
            (root / "main.py").write_text("def main():\n    return 1\n", encoding="utf-8")
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "initial")
            self._git(root, "branch", "base")
            self._git(root, "checkout", "-q", "-b", "feature")
            (root / "main.py").write_text("def main():\n    value = 1\n    return value\n", encoding="utf-8")
            (root / "new.py").write_text("def new():\n    return True\n", encoding="utf-8")
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "add feature module")

            comparison = git_compare(root, "base", "feature")

            self.assertTrue(comparison["available"])
            self.assertEqual(comparison["base_branch"], "base")
            self.assertEqual(comparison["target_branch"], "feature")
            self.assertEqual(comparison["commit_count"], 1)
            self.assertEqual(comparison["lines_added"], 4)
            self.assertEqual(comparison["lines_deleted"], 1)
            files = {item["path"]: item for item in comparison["changed_files"]}
            self.assertEqual(files["main.py"]["status"], "M")
            self.assertEqual(files["main.py"]["lines_added"], 2)
            self.assertEqual(files["new.py"]["status"], "A")

            report = scan_repository(root)
            main = next(item for item in report.files if item.path == "main.py")
            self.assertTrue(main.last_commit)
            self.assertEqual(main.last_commit_subject, "add feature module")

    def test_compare_and_impact_routes_accept_branch_refs(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as database:
            root = Path(directory)
            self._git(root, "init", "-q")
            (root / "main.py").write_text("def main():\n    return 1\n", encoding="utf-8")
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "initial")
            self._git(root, "branch", "base")
            self._git(root, "checkout", "-q", "-b", "feature")
            (root / "main.py").write_text("def main():\n    value = 1\n    return value\n", encoding="utf-8")
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "feature change")

            app = create_app({"DATABASE_PATH": str(Path(database) / "test.sqlite3")})
            client = app.test_client()
            try:
                self.assertEqual(client.post("/scan", json={"repo_path": str(root)}).status_code, 200)
                comparison = client.get("/api/repository/compare?base_branch=base&target_branch=feature").get_json()
                self.assertTrue(comparison["git"]["available"])
                self.assertEqual(comparison["git"]["base_branch"], "base")
                self.assertTrue(any(item["path"] == "main.py" for item in comparison["changed_files"]))
                main_change = next(item for item in comparison["changed_files"] if item["path"] == "main.py")
                self.assertIn("lines_added", main_change)

                raw_diff = client.get("/api/git/compare?base=base&target=feature").get_json()
                self.assertEqual(raw_diff["view"], "git_compare")
                self.assertEqual(raw_diff["changed_file_count"], 1)

                impact = client.get("/api/change-impact?file=main.py&base=base&target=feature").get_json()
                self.assertEqual(impact["git_context"]["comparison"]["target_branch"], "feature")
                self.assertEqual(impact["git_diff"]["path"], "main.py")
            finally:
                app.extensions["repopilot_store"].close()

    @staticmethod
    def _git(root: Path, *args: str) -> None:
        subprocess.run(
            ["git", "-C", str(root), "-c", "user.name=RepoPilot Test", "-c", "user.email=test@repopilot.local", *args],
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
