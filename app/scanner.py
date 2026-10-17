"""Local repository scanner and explainable risk scoring model."""

from __future__ import annotations

import ast
import hashlib
import os
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .demo_data import demo_report
from .models import FileAnalysis, RepositoryReport, RiskSignal

SUPPORTED = {".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rb", ".rs", ".md"}
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache"}


def _level(score: int) -> str:
    if score >= 75:
        return "critical" if score >= 90 else "high"
    if score >= 40:
        return "medium"
    return "low"


def _git_change_counts(root: Path, paths: list[Path]) -> dict[str, int]:
    """Return recent commit counts when a local git history is available."""
    result: dict[str, int] = {}
    if not (root / ".git").exists():
        return result
    for path in paths:
        try:
            output = subprocess.run(
                ["git", "-C", str(root), "log", "--since=30 days ago", "--oneline", "--", str(path.relative_to(root))],
                capture_output=True, text=True, timeout=2, check=False,
            ).stdout
            result[path.as_posix()] = len([line for line in output.splitlines() if line.strip()])
        except (OSError, subprocess.SubprocessError, ValueError):
            result[path.as_posix()] = 0
    return result


def _python_metrics(content: str) -> tuple[int, int]:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return 0, sum(content.count(token) for token in ("if ", "for ", "while ", "&&", "||"))
    functions = sum(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) for node in ast.walk(tree))
    branches = sum(isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.Match, ast.BoolOp)) for node in ast.walk(tree))
    return functions, branches


def _imports(path: Path, content: str, relative_paths: set[str]) -> list[str]:
    candidates: set[str] = set()
    if path.suffix == ".py":
        try:
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    candidates.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    candidates.add(node.module.split(".")[0])
        except SyntaxError:
            pass
    else:
        # A deliberately small, dependency-free heuristic for common import syntax.
        import re
        candidates.update(re.findall(r"(?:from|import)\s+[\"']([^\"']+)", content))
    matches = []
    stem = path.stem
    for other in relative_paths:
        other_stem = Path(other).stem
        if other != path.as_posix() and (other_stem in candidates or other_stem in " ".join(candidates) or stem in other):
            matches.append(other)
    return sorted(set(matches))


def scan_repository(repository_path: str | os.PathLike[str] | None) -> RepositoryReport:
    """Scan a local path, falling back to deterministic demo data if absent/invalid."""
    if not repository_path or str(repository_path).strip().lower() in {"demo", "demo://repo-pilot", "example"}:
        return demo_report()
    root = Path(repository_path).expanduser()
    if not root.is_dir():
        return demo_report()

    paths = sorted(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in SUPPORTED and not any(part in SKIP_DIRS for part in path.parts))
    paths = paths[:250]
    if not paths:
        return demo_report()
    rel_paths = {path.relative_to(root).as_posix() for path in paths}
    changes = _git_change_counts(root, paths)
    dependencies: dict[str, list[str]] = {}
    contents: dict[str, str] = {}
    for path in paths:
        rel = path.relative_to(root).as_posix()
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            content = ""
        contents[rel] = content
        dependencies[rel] = _imports(path, content, rel_paths)
    dependents: dict[str, list[str]] = defaultdict(list)
    for source, targets in dependencies.items():
        for target in targets:
            dependents[target].append(source)

    analyses: list[FileAnalysis] = []
    for path in paths:
        rel = path.relative_to(root).as_posix()
        content = contents[rel]
        lines = len(content.splitlines())
        functions, branches = _python_metrics(content) if path.suffix == ".py" else (content.count("function ") + content.count("def "), content.count("if ") + content.count("for "))
        change_count = changes.get(path.as_posix(), 0)
        if change_count == 0:
            # Keep the model useful for non-git folders while remaining deterministic.
            change_count = int(hashlib.sha1(content.encode()).hexdigest()[:2], 16) % 4
        complexity = min(100, round((lines / 4) + (branches * 4) + (functions * 1.5)))
        frequency = min(100, change_count * 11)
        reach = min(100, len(dependencies[rel]) * 12 + len(dependents[rel]) * 20)
        risk = round(complexity * 0.45 + frequency * 0.25 + reach * 0.30)
        signals = [
            RiskSignal("complexity", complexity, _level(complexity), "Complexity combines file size, functions, and branching.", [f"{functions} functions", f"{branches} branch points", f"{lines} lines"]),
            RiskSignal("change_frequency", frequency, _level(frequency), "Change frequency uses commits from the last 30 days when git history is available.", [f"{change_count} recent changes"]),
            RiskSignal("dependency_reach", reach, _level(reach), "Dependency reach combines direct imports and downstream dependents.", [f"{len(dependencies[rel])} direct dependencies", f"{len(dependents[rel])} downstream dependents"]),
        ]
        analyses.append(FileAnalysis(rel, _language(path.suffix), lines, functions, dependencies[rel], sorted(dependents[rel]), risk, _level(risk), signals, _summary(risk, len(dependents[rel])), content))
    return RepositoryReport(
        repository={"name": root.name, "path": str(root), "branch": _git_branch(root), "commit": _git_commit(root)},
        files=analyses,
        generated_at=datetime.now(timezone.utc).isoformat(),
        demo_mode=False,
    )


def _language(suffix: str) -> str:
    return {".py": "Python", ".js": "JavaScript", ".jsx": "React JSX", ".ts": "TypeScript", ".tsx": "React TSX", ".java": "Java", ".go": "Go", ".rb": "Ruby", ".rs": "Rust", ".md": "Markdown"}.get(suffix, "Text")


def _summary(risk: int, dependents: int) -> str:
    if risk >= 75:
        return "High-risk module; prioritize review before broad changes." + (" It also has downstream consumers." if dependents else "")
    if risk >= 40:
        return "Moderate-risk module with useful review signals."
    return "Low-risk module with limited change impact."


def _git_value(root: Path, args: list[str], default: str) -> str:
    try:
        value = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=2, check=False).stdout.strip()
        return value or default
    except (OSError, subprocess.SubprocessError):
        return default


def _git_branch(root: Path) -> str:
    return _git_value(root, ["branch", "--show-current"], "working-tree")


def _git_commit(root: Path) -> str:
    return _git_value(root, ["rev-parse", "--short", "HEAD"], "uncommitted")
