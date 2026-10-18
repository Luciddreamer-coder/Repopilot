"""Build a deterministic, temporary Git checkout for the experiment fixture.

The fixture deliberately does not contain ``.git`` data.  This helper turns a
copy into a small repository from the checked-in ``history.json`` manifest so
experiments can exercise Git metrics without making the workspace itself a
nested repository.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


DEFAULT_COMMIT_DATE = "2026-10-19T12:00:00+0000"


def _git(root: Path, *args: str, env: dict[str, str] | None = None) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


def materialize_fixture_git(source: Path | str, destination: Path | str) -> Path:
    """Copy ``source`` to ``destination`` and replay its checked-in history.

    Commit identity, timestamps, branch name, and file ordering are fixed, so
    two materializations have identical commit IDs.  The source fixture is
    never modified.  A fixture without ``history.json`` is simply copied and
    returned, which keeps the helper useful for small ad-hoc experiments.
    """
    source_path = Path(source).resolve()
    destination_path = Path(destination).resolve()
    if destination_path.exists():
        raise FileExistsError(destination_path)
    shutil.copytree(source_path, destination_path, ignore=shutil.ignore_patterns(".git"))
    manifest_path = source_path / "history.json"
    if not manifest_path.is_file():
        return destination_path

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    commits = manifest.get("commits", [])
    paths = {
        entry["path"]
        for commit in commits
        for entry in commit.get("changed_files", [])
        if entry.get("path")
    }
    # Start with only metadata files (annotations and the history manifest).
    # Each source file enters the checkout at the commit declared by the
    # manifest, rather than being accidentally present in the first commit.
    for relative in paths:
        candidate = destination_path / relative
        if candidate.exists():
            if candidate.is_dir():
                shutil.rmtree(candidate)
            else:
                candidate.unlink()

    _git(destination_path, "init", "-q")
    _git(destination_path, "branch", "-M", manifest.get("branch", "main"))
    base_environment = os.environ.copy()
    base_environment.update(
        {
            "GIT_AUTHOR_NAME": "RepoPilot Fixture",
            "GIT_AUTHOR_EMAIL": "fixture@repopilot.local",
            "GIT_COMMITTER_NAME": "RepoPilot Fixture",
            "GIT_COMMITTER_EMAIL": "fixture@repopilot.local",
        }
    )
    for index, commit in enumerate(commits):
        changed = commit.get("changed_files", [])
        for entry in changed:
            relative = entry["path"]
            target = destination_path / relative
            status = str(entry.get("status", "A"))[:1]
            if status == "D":
                if target.exists():
                    target.unlink()
            else:
                source_file = source_path / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_file, target)
        staged = [entry["path"] for entry in changed if entry.get("path")]
        if staged:
            _git(destination_path, "add", "--", *sorted(staged), env=base_environment)
        environment = base_environment.copy()
        environment["GIT_AUTHOR_DATE"] = commit.get("date", DEFAULT_COMMIT_DATE)
        environment["GIT_COMMITTER_DATE"] = commit.get("date", DEFAULT_COMMIT_DATE)
        # Empty commits preserve the manifest even when a fixture intentionally
        # records a metadata-only revision.
        _git(
            destination_path,
            "commit",
            "--quiet",
            "--allow-empty",
            "-m",
            commit.get("subject", f"fixture revision {index + 1}"),
            env=environment,
        )
    return destination_path
