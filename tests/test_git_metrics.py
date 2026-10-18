import subprocess
import tempfile
import unittest
from pathlib import Path

from app.scanner import scan_repository


class RepoPilotGitMetricTests(unittest.TestCase):
    def test_scan_collects_recent_git_churn_and_repository_activity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._git(root, "init", "-q")
            (root / "main.py").write_text("def main():\n    return 1\n", encoding="utf-8")
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "initial")
            (root / "main.py").write_text("def main():\n    value = 1\n    return value\n", encoding="utf-8")
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "refine")

            report = scan_repository(root)
            item = next(file for file in report.files if file.path == "main.py")

            self.assertGreaterEqual(item.change_count, 2)
            self.assertGreater(item.lines_added, 0)
            self.assertGreaterEqual(item.authors, 1)
            self.assertTrue(item.last_changed)
            self.assertGreaterEqual(report.repository["git"]["recent_commits"], 2)

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
