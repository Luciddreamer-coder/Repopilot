"""Resolve local folders and GitHub HTTPS sources for repository scans."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit, urlunsplit


class RepositorySourceError(ValueError):
    """A repository source could not be validated or prepared."""


@dataclass(frozen=True)
class ResolvedRepository:
    path: Path
    source_type: str
    source_url: str | None = None


def normalize_github_url(value: str) -> str:
    """Validate and normalize an allowed HTTPS Git repository URL."""
    parsed = urlsplit(value.strip())
    hostname = (parsed.hostname or "").lower()
    allowed_hosts = {"github.com", "www.github.com"}
    configured_hosts = os.environ.get("REPOPILOT_ALLOWED_GIT_HOSTS", "")
    allowed_hosts.update(host.strip().lower() for host in configured_hosts.split(",") if host.strip())
    if parsed.scheme.lower() != "https" or hostname not in allowed_hosts:
        raise RepositorySourceError("Use a local folder path or an HTTPS GitHub URL on an allowed host (GitHub is allowed by default).")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RepositorySourceError("Git repository URLs must not contain credentials, query parameters, or fragments.")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) != 2 or not all(parts):
        raise RepositorySourceError("Git repository URL must point to a repository: https://github.com/org/repo.git.")
    repository = parts[1][:-4] if parts[1].lower().endswith(".git") else parts[1]
    if not repository:
        raise RepositorySourceError("GitHub URL must include a repository name.")
    output_host = "github.com" if hostname == "www.github.com" else hostname
    return urlunsplit(("https", output_host, f"/{parts[0]}/{repository}.git", "", ""))


def _cache_root(cache_root: str | os.PathLike[str] | None = None) -> Path:
    configured = cache_root or os.environ.get("REPOPILOT_REMOTE_CACHE")
    return Path(configured).expanduser() if configured else Path(tempfile.gettempdir()) / "repopilot-github-cache"


def _clone_error(result: subprocess.CompletedProcess[str]) -> str:
    details = (result.stderr or result.stdout or "").strip().splitlines()
    return details[-1] if details else "Git could not clone the repository."


def _directory_size(root: Path) -> int:
    return sum(item.stat().st_size for item in root.rglob("*") if item.is_file())


def _within_size_limit(root: Path) -> bool:
    limit_mb = int(os.environ.get("REPOPILOT_REMOTE_MAX_MB", "500"))
    return _directory_size(root) <= limit_mb * 1024 * 1024


def _clone_github(url: str, cache_root: Path, timeout: int) -> Path:
    repository_name = url.rstrip("/").rsplit("/", 1)[-1][:-4]
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    destination = cache_root / f"{repository_name}-{key}"
    if (destination / ".git").is_dir():
        if _within_size_limit(destination):
            return destination
        shutil.rmtree(destination, ignore_errors=True)
    cache_root.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        shutil.rmtree(destination, ignore_errors=True)
    try:
        result = subprocess.run(
            ["git", "clone", "--depth", "1", "--no-tags", url, str(destination)],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RepositorySourceError("Git is required to scan a GitHub URL, but it was not found on this machine.") from exc
    except subprocess.TimeoutExpired as exc:
        shutil.rmtree(destination, ignore_errors=True)
        raise RepositorySourceError(f"Cloning the repository timed out after {timeout} seconds.") from exc
    if result.returncode != 0 or not (destination / ".git").is_dir():
        shutil.rmtree(destination, ignore_errors=True)
        raise RepositorySourceError(f"Git could not clone the repository: {_clone_error(result)}")
    if not _within_size_limit(destination):
        shutil.rmtree(destination, ignore_errors=True)
        limit_mb = int(os.environ.get("REPOPILOT_REMOTE_MAX_MB", "500"))
        raise RepositorySourceError(f"The cloned repository exceeds the {limit_mb} MB safety limit.")
    return destination


def resolve_repository_source(value: str | os.PathLike[str] | None, *, cache_root: str | os.PathLike[str] | None = None, timeout: int = 30) -> ResolvedRepository | None:
    """Return a local scan path, cloning a GitHub HTTPS source when needed."""
    if value is None or not str(value).strip():
        return None
    raw = str(value).strip()
    if raw.lower().startswith(("http://", "https://")):
        url = normalize_github_url(raw)
        configured_timeout = int(os.environ.get("REPOPILOT_REMOTE_TIMEOUT", str(timeout)))
        return ResolvedRepository(_clone_github(url, _cache_root(cache_root), configured_timeout), "github", url)
    path = Path(raw).expanduser()
    if not path.is_dir():
        raise RepositorySourceError("Local repository folder was not found. Check the path and try again.")
    return ResolvedRepository(path, "local")
