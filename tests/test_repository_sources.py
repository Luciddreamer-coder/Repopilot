import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.repository_sources import RepositorySourceError, normalize_github_url, resolve_repository_source
from app.scanner import scan_repository


class RepositorySourceTests(unittest.TestCase):
    def test_normalizes_github_https_urls(self):
        self.assertEqual(
            normalize_github_url("https://www.github.com/acme/widgets"),
            "https://github.com/acme/widgets.git",
        )

    def test_rejects_non_github_or_credential_urls(self):
        with self.assertRaises(RepositorySourceError):
            normalize_github_url("http://github.com/acme/widgets.git")
        with self.assertRaises(RepositorySourceError):
            normalize_github_url("https://token@github.com/acme/widgets.git")

    def test_allows_configured_https_git_hosts_without_credentials(self):
        with patch.dict(os.environ, {"REPOPILOT_ALLOWED_GIT_HOSTS": "git.example.com"}, clear=False):
            self.assertEqual(
                normalize_github_url("https://git.example.com/acme/widgets"),
                "https://git.example.com/acme/widgets.git",
            )

    def test_github_checkout_is_cached_and_scannable_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "cache"

            def fake_git(command, **_kwargs):
                if command[1] == "clone":
                    destination = Path(command[-1])
                    (destination / ".git").mkdir(parents=True)
                    (destination / "main.py").write_text("def main():\n    return 1\n", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch.dict(os.environ, {"REPOPILOT_REMOTE_CACHE": str(cache)}, clear=False), patch("app.repository_sources.subprocess.run", side_effect=fake_git) as run:
                first = resolve_repository_source("https://github.com/acme/widgets.git")
                second = resolve_repository_source("https://github.com/acme/widgets")
                report = scan_repository("https://github.com/acme/widgets")

            self.assertEqual(first, second)
            clone_calls = [call for call in run.call_args_list if call.args[0][1] == "clone"]
            self.assertEqual(len(clone_calls), 1)
            self.assertEqual(report.repository["source_type"], "github")
            self.assertEqual(report.repository["source_url"], "https://github.com/acme/widgets.git")
            self.assertEqual(report.files[0].path, "main.py")

    def test_github_checkout_respects_size_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "cache"

            def fake_git(command, **_kwargs):
                destination = Path(command[-1])
                (destination / ".git").mkdir(parents=True)
                (destination / "large.txt").write_text("too large", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch.dict(os.environ, {"REPOPILOT_REMOTE_CACHE": str(cache), "REPOPILOT_REMOTE_MAX_MB": "0"}, clear=False), patch("app.repository_sources.subprocess.run", side_effect=fake_git):
                with self.assertRaisesRegex(RepositorySourceError, "safety limit"):
                    resolve_repository_source("https://github.com/acme/large-repo")


if __name__ == "__main__":
    unittest.main()
