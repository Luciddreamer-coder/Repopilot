"""Local repository scanner and explainable risk scoring model."""

from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
import os
import re
import sqlite3
import subprocess
import xml.etree.ElementTree as ET
from collections import OrderedDict, defaultdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .demo_data import demo_report
from .models import FileAnalysis, RepositoryReport, RiskSignal

SUPPORTED = {".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rb", ".rs", ".md"}
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache"}
LOW_COVERAGE_THRESHOLD = 80.0
_SCAN_CACHE_LIMIT = 8
_SCAN_CACHE: OrderedDict[tuple[object, ...], RepositoryReport] = OrderedDict()


def _level(score: int) -> str:
    if score >= 75:
        return "critical" if score >= 90 else "high"
    if score >= 40:
        return "medium"
    return "low"


def _git_file_metrics(root: Path, paths: list[Path]) -> dict[str, dict[str, object]]:
    """Return recent commit, churn, author, and recency metrics per file."""
    result: dict[str, dict[str, object]] = {}
    if not (root / ".git").exists():
        return result
    for path in paths:
        try:
            output = subprocess.run(
                ["git", "-C", str(root), "log", "--follow", "--since=90 days ago", "--numstat", "--format=%H%x1f%h%x1f%an%x1f%ae%x1f%ad%x1f%s", "--date=iso-strict", "--", str(path.relative_to(root))],
                capture_output=True, text=True, timeout=2, check=False,
            ).stdout
            commits = 0
            lines_added = 0
            lines_deleted = 0
            authors: set[str] = set()
            last_changed: str | None = None
            last_commit: str | None = None
            last_commit_short: str | None = None
            last_commit_subject: str | None = None
            last_author: str | None = None
            last_author_email: str | None = None
            for line in output.splitlines():
                if "\x1f" in line:
                    fields = line.split("\x1f", 5)
                    if len(fields) < 6:
                        continue
                    last_commit = last_commit or fields[0].strip()
                    last_commit_short = last_commit_short or fields[1].strip()
                    last_author = last_author or fields[2].strip()
                    last_author_email = last_author_email or fields[3].strip()
                    changed_at = fields[4]
                    last_commit_subject = last_commit_subject or fields[5].strip()
                    commits += 1
                    authors.add(fields[2].strip())
                    last_changed = last_changed or changed_at.strip()
                    continue
                parts = line.split("\t")
                if len(parts) < 3:
                    continue
                try:
                    if parts[0] != "-":
                        lines_added += int(parts[0])
                    if parts[1] != "-":
                        lines_deleted += int(parts[1])
                except ValueError:
                    continue
            result[path.as_posix()] = {
                "change_count": commits,
                "lines_added": lines_added,
                "lines_deleted": lines_deleted,
                "authors": len(authors),
                "last_changed": last_changed,
                "last_commit": last_commit,
                "last_commit_short": last_commit_short,
                "last_commit_subject": last_commit_subject,
                "last_author": last_author,
                "last_author_email": last_author_email,
            }
        except (OSError, subprocess.SubprocessError, ValueError):
            result[path.as_posix()] = {}
    return result


def _git_repository_metrics(root: Path) -> dict[str, object]:
    """Return a compact repository-level Git activity summary."""
    if not (root / ".git").exists():
        return {"window_days": 90, "recent_commits": 0, "contributors": 0, "last_commit": None}
    try:
        output = subprocess.run(
            ["git", "-C", str(root), "log", "--since=90 days ago", "--format=%H%x1f%an%x1f%ad", "--date=iso-strict"],
            capture_output=True, text=True, timeout=2, check=False,
        ).stdout
        rows = [line.split("\x1f", 2) for line in output.splitlines() if "\x1f" in line]
        authors = {row[1].strip() for row in rows if len(row) > 1 and row[1].strip()}
        return {
            "window_days": 90,
            "recent_commits": len(rows),
            "contributors": len(authors),
            "last_commit": rows[0][2].strip() if rows and len(rows[0]) > 2 else None,
        }
    except (OSError, subprocess.SubprocessError, ValueError):
        return {"window_days": 90, "recent_commits": 0, "contributors": 0, "last_commit": None}


def _python_metrics(content: str) -> tuple[int, int]:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return 0, sum(content.count(token) for token in ("if ", "for ", "while ", "&&", "||"))
    functions = sum(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) for node in ast.walk(tree))
    branches = sum(isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.Match, ast.BoolOp)) for node in ast.walk(tree))
    return functions, branches


def _normalize_path(value: str) -> str:
    return value.replace("\\", "/").lstrip("./")


def _python_module_index(relative_paths: set[str]) -> dict[str, str]:
    """Map importable dotted Python names to analyzed paths.

    The index intentionally contains only files in the current scan.  This
    makes the dependency graph conservative: third-party and stdlib imports
    are unresolved instead of being guessed as local relationships.
    """
    index: dict[str, str] = {}
    for relative in sorted(relative_paths):
        if not relative.endswith(".py"):
            continue
        path = PurePosixPath(relative)
        parts = list(path.parts)
        if parts[-1] == "__init__.py":
            name_parts = parts[:-1]
        else:
            name_parts = parts[:-1] + [path.stem]
        if name_parts:
            index[".".join(name_parts)] = relative
            # ``src`` and similar source roots are commonly added to
            # PYTHONPATH, so permit imports that intentionally omit that
            # filesystem-only prefix.  Do not add aliases for arbitrary
            # package directories; that would invent edges for ``import x``.
            if name_parts[0] in {"src", "source", "lib", "python"} and len(name_parts) > 1:
                index[".".join(name_parts[1:])] = relative
    return index


def _python_import_candidates(path: Path, node: ast.Import | ast.ImportFrom, relative_paths: set[str], index: dict[str, str]) -> list[tuple[str, str]]:
    """Return ``(displayed import, dotted candidate)`` pairs for a node."""
    result: list[tuple[str, str]] = []
    if isinstance(node, ast.Import):
        for alias in node.names:
            result.append((alias.name, alias.name))
        return result

    module = node.module or ""
    if node.level:
        # ``path.parent`` is the package containing the current module.  A
        # level of one stays in that package; each additional dot climbs one
        # package, matching Python's ImportFrom semantics.
        package_parts = list(path.parent.parts)
        climb = max(node.level - 1, 0)
        if climb > len(package_parts):
            package_parts = []
        else:
            package_parts = package_parts[: len(package_parts) - climb]
        base = ".".join(package_parts + ([module] if module else []))
        shown = "." * node.level + module
        if module:
            result.append((shown, base))
        for alias in node.names:
            # ``from . import sibling`` names the sibling module directly.
            if not module:
                result.append((shown + alias.name, ".".join(package_parts + [alias.name])))
            elif base not in index:
                # If the imported module itself is not present, a submodule
                # may still be present (``from .package import module``).
                result.append((shown + " " + alias.name, base + "." + alias.name))
        return result

    if module:
        shown = module
        result.append((shown, module))
        # A module imported from a package may be represented by the package
        # file or by one of its child modules.
        if module in index:
            for alias in node.names:
                result.append((f"{module}.{alias.name}", f"{module}.{alias.name}"))
    return result


def _resolve_python_name(name: str, index: dict[str, str]) -> str | None:
    candidate = name.lstrip(".")
    while candidate:
        if candidate in index:
            return index[candidate]
        candidate = candidate.rsplit(".", 1)[0] if "." in candidate else ""
    return None


def _python_imports(path: Path, content: str, relative_paths: set[str]) -> tuple[list[str], list[str]]:
    dependencies: set[str] = set()
    unresolved: set[str] = set()
    index = _python_module_index(relative_paths)
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return [], []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        node_resolved = False
        node_unresolved: set[str] = set()
        for displayed, candidate in _python_import_candidates(path, node, relative_paths, index):
            target = _resolve_python_name(candidate, index)
            if target and target != path.as_posix():
                dependencies.add(target)
                node_resolved = True
            elif not target:
                node_unresolved.add(displayed)
        if node_unresolved and not node_resolved:
            if isinstance(node, ast.ImportFrom) and node.module:
                unresolved.add("." * node.level + node.module)
            else:
                unresolved.update(node_unresolved)
    return sorted(dependencies), sorted(unresolved)


def _non_python_imports(path: Path, content: str, relative_paths: set[str]) -> tuple[list[str], list[str]]:
    """Resolve only explicit file/module names for non-Python source files."""
    if path.suffix.lower() in {".js", ".jsx", ".ts", ".tsx"}:
        patterns = (
            r"\bfrom\s+[\"']([^\"']+)[\"']",
            r"\bimport\s+[\"']([^\"']+)[\"']",
            r"\bexport\s+[^;\n]*?\s+from\s+[\"']([^\"']+)[\"']",
            r"\brequire\s*\(\s*[\"']([^\"']+)[\"']\s*\)",
        )
    elif path.suffix.lower() == ".java":
        patterns = (r"\bimport\s+([A-Za-z_][\w.]*)\s*;",)
    elif path.suffix.lower() == ".go":
        patterns = (r"\bimport\s*(?:\([^)]*?\s)?[\"']([^\"']+)[\"']",)
    elif path.suffix.lower() == ".rb":
        patterns = (r"\brequire(?:_relative)?\s+[\"']([^\"']+)[\"']",)
    else:
        patterns = (r"\bfrom\s+[\"']([^\"']+)[\"']", r"\bimport\s+[\"']([^\"']+)[\"']")
    candidates: list[str] = []
    for pattern in patterns:
        candidates.extend(re.findall(pattern, content))
    candidates = sorted(set(candidates))
    normalized = {_normalize_path(item): item for item in relative_paths}
    dependencies: set[str] = set()
    unresolved: set[str] = set()
    source_parent = path.parent
    for candidate in candidates:
        if candidate.startswith("."):
            base = PurePosixPath(source_parent.as_posix()) / candidate
            base_name = base.as_posix()
        else:
            base_name = candidate.replace(".", "/")
        base_name = _normalize_path(base_name)
        options = [base_name]
        if Path(base_name).suffix == "":
            options.extend([base_name + suffix for suffix in (".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rb", ".rs")])
            options.extend([base_name + "/" + init for init in ("index.js", "index.ts", "__init__.py")])
        target = next((normalized.get(_normalize_path(option)) for option in options if _normalize_path(option) in normalized), None)
        if target and target != path.as_posix():
            dependencies.add(target)
        elif not target:
            unresolved.add(candidate)
    return sorted(dependencies), sorted(unresolved)


def _imports_with_unresolved(path: Path, content: str, relative_paths: set[str]) -> tuple[list[str], list[str]]:
    if path.suffix.lower() == ".py":
        return _python_imports(path, content, relative_paths)
    return _non_python_imports(path, content, relative_paths)


def _imports(path: Path, content: str, relative_paths: set[str]) -> list[str]:
    """Compatibility wrapper returning only resolved local imports."""
    return _imports_with_unresolved(path, content, relative_paths)[0]


def _coverage_path(root: Path, raw_path: str, relative_paths: set[str]) -> str | None:
    value = raw_path.strip().replace("\\", "/")
    if not value:
        return None
    value = re.sub(r"^[A-Za-z]:/", "/", value)
    candidates = [value.lstrip("/")]
    try:
        candidates.append(Path(value).resolve().relative_to(root.resolve()).as_posix())
    except (OSError, ValueError):
        pass
    for candidate in candidates:
        candidate = candidate.lstrip("./")
        if candidate in relative_paths:
            return candidate
    suffix_matches = [path for path in relative_paths if path.endswith("/" + value.lstrip("/")) or path == value.lstrip("/")]
    if not suffix_matches:
        basename = PurePosixPath(value).name
        suffix_matches = [path for path in relative_paths if PurePosixPath(path).name == basename]
    return sorted(suffix_matches)[0] if len(suffix_matches) == 1 else None


def _coverage_entry(covered: int, total: int, source: str) -> dict[str, object] | None:
    if total <= 0:
        return None
    covered = max(0, min(covered, total))
    return {
        "percent": round(covered * 100 / total, 2),
        "covered_lines": covered,
        "total_lines": total,
        "missing_lines": total - covered,
        "source": source,
    }


def _parse_lcov(root: Path, relative_paths: set[str]) -> dict[str, dict[str, object]]:
    report = root / "lcov.info"
    if not report.is_file():
        return {}
    result: dict[str, dict[str, object]] = {}
    current: str | None = None
    line_hits: dict[int, int] = {}
    found: int | None = None
    hit: int | None = None
    try:
        lines = report.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    def finish() -> None:
        nonlocal current, line_hits, found, hit
        if current:
            total = found if found is not None else len(line_hits)
            covered = hit if hit is not None else sum(value > 0 for value in line_hits.values())
            entry = _coverage_entry(covered, total, "lcov.info")
            if entry:
                result[current] = entry
        current, line_hits, found, hit = None, {}, None, None
    for line in lines:
        if line.startswith("SF:"):
            finish()
            current = _coverage_path(root, line[3:], relative_paths)
        elif line.startswith("DA:"):
            fields = line[3:].split(",")
            if len(fields) >= 2:
                try:
                    line_hits[int(fields[0])] = int(fields[1])
                except ValueError:
                    pass
        elif line.startswith("LF:"):
            try:
                found = int(line[3:])
            except ValueError:
                pass
        elif line.startswith("LH:"):
            try:
                hit = int(line[3:])
            except ValueError:
                pass
        elif line == "end_of_record":
            finish()
    finish()
    return result


def _parse_coverage_xml(root: Path, relative_paths: set[str]) -> dict[str, dict[str, object]]:
    report = root / "coverage.xml"
    if not report.is_file():
        return {}
    try:
        document = ET.parse(report)
    except (OSError, ET.ParseError):
        return {}
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    class_nodes = [node for node in document.getroot().iter() if node.tag.rsplit("}", 1)[-1] == "class"]
    for class_node in class_nodes:
        filename = class_node.attrib.get("filename", "")
        relative = _coverage_path(root, filename, relative_paths)
        if not relative:
            continue
        line_nodes = [node for node in class_node.iter() if node.tag.rsplit("}", 1)[-1] == "line"]
        for line_node in line_nodes:
            counts[relative][1] += 1
            try:
                counts[relative][0] += int(float(line_node.attrib.get("hits", "0"))) > 0
            except ValueError:
                pass
    return {
        relative: entry
        for relative, (covered, total) in counts.items()
        if (entry := _coverage_entry(covered, total, "coverage.xml")) is not None
    }


def _parse_coverage_database(root: Path, relative_paths: set[str]) -> dict[str, dict[str, object]]:
    """Best-effort reader for old coverage.py ``line`` tables.

    Newer coverage.py releases store compact ``line_bits`` blobs, which are
    deliberately left alone here because decoding them would duplicate a
    third-party implementation.  XML/LCOV remain the fully supported formats.
    """
    database = root / ".coverage"
    if not database.is_file():
        return {}
    try:
        connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"file", "line"}.issubset(tables):
            connection.close()
            return {}
        rows = connection.execute("SELECT file.path, COUNT(line.line) FROM file JOIN line ON line.file_id = file.id GROUP BY file.path").fetchall()
        connection.close()
    except (OSError, sqlite3.Error):
        return {}
    result: dict[str, dict[str, object]] = {}
    for raw_path, covered in rows:
        relative = _coverage_path(root, str(raw_path), relative_paths)
        if not relative:
            continue
        source_lines = len((root / relative).read_text(encoding="utf-8", errors="replace").splitlines())
        entry = _coverage_entry(int(covered), source_lines, ".coverage")
        if entry:
            result[relative] = entry
    return result


def _load_coverage(root: Path, relative_paths: set[str]) -> dict[str, dict[str, object]]:
    """Read available local coverage reports, preferring XML then LCOV."""
    result = _parse_coverage_xml(root, relative_paths)
    for relative, entry in _parse_lcov(root, relative_paths).items():
        result.setdefault(relative, entry)
    for relative, entry in _parse_coverage_database(root, relative_paths).items():
        result.setdefault(relative, entry)
    return result


def _dependency_cycles(dependencies: dict[str, list[str]]) -> list[list[str]]:
    """Return deterministic strongly-connected dependency components."""
    index = 0
    stack: list[str] = []
    on_stack: set[str] = set()
    indexes: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    cycles: list[list[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indexes[node] = lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for target in dependencies.get(node, []):
            if target not in indexes:
                visit(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in on_stack:
                lowlinks[node] = min(lowlinks[node], indexes[target])
        if lowlinks[node] == indexes[node]:
            component: list[str] = []
            while True:
                target = stack.pop()
                on_stack.remove(target)
                component.append(target)
                if target == node:
                    break
            component.sort()
            if len(component) > 1 or component[0] in dependencies.get(component[0], []):
                cycles.append(component)

    for node in sorted(dependencies):
        if node not in indexes:
            visit(node)
    return sorted(cycles)


def _scan_cache_key(root: Path, paths: list[Path]) -> tuple[object, ...]:
    """Build a bounded, local cache key that invalidates on normal file edits."""
    signature = []
    for path in paths:
        try:
            stat = path.stat()
            signature.append((path.relative_to(root).as_posix(), stat.st_mtime_ns, stat.st_size))
        except OSError:
            signature.append((path.relative_to(root).as_posix(), None, None))
    metadata = []
    for name in ("coverage.xml", "lcov.info", ".coverage"):
        candidate = root / name
        try:
            stat = candidate.stat()
            metadata.append((name, stat.st_mtime_ns, stat.st_size))
        except OSError:
            metadata.append((name, None, None))
    head = _git_value(root, ["rev-parse", "HEAD"], "") if (root / ".git").exists() else ""
    return (str(root.resolve()), head, tuple(signature), tuple(metadata))


def _cache_report(key: tuple[object, ...], report: RepositoryReport) -> RepositoryReport:
    _SCAN_CACHE[key] = deepcopy(report)
    _SCAN_CACHE.move_to_end(key)
    while len(_SCAN_CACHE) > _SCAN_CACHE_LIMIT:
        _SCAN_CACHE.popitem(last=False)
    return report


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
    cache_key = _scan_cache_key(root, paths)
    cached = _SCAN_CACHE.get(cache_key)
    if cached is not None:
        _SCAN_CACHE.move_to_end(cache_key)
        return deepcopy(cached)
    rel_paths = {path.relative_to(root).as_posix() for path in paths}
    git_metrics = _git_file_metrics(root, paths)
    coverage = _load_coverage(root, rel_paths)
    dependencies: dict[str, list[str]] = {}
    unresolved_imports: dict[str, list[str]] = {}
    contents: dict[str, str] = {}
    for path in paths:
        rel = path.relative_to(root).as_posix()
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            content = ""
        contents[rel] = content
        # Resolve imports against repository-relative paths, never against the
        # machine-specific absolute path used for file I/O.
        dependencies[rel], unresolved_imports[rel] = _imports_with_unresolved(Path(rel), content, rel_paths)
    dependents: dict[str, list[str]] = defaultdict(list)
    for source, targets in dependencies.items():
        for target in targets:
            dependents[target].append(source)
    cycles = _dependency_cycles(dependencies)
    cycles_by_file: dict[str, list[list[str]]] = defaultdict(list)
    for cycle in cycles:
        for member in cycle:
            cycles_by_file[member].append(cycle)

    analyses: list[FileAnalysis] = []
    for path in paths:
        rel = path.relative_to(root).as_posix()
        content = contents[rel]
        lines = len(content.splitlines())
        functions, branches = _python_metrics(content) if path.suffix == ".py" else (content.count("function ") + content.count("def "), content.count("if ") + content.count("for "))
        file_git = git_metrics.get(path.as_posix(), {})
        change_count = int(file_git.get("change_count", 0) or 0)
        if change_count == 0:
            # Keep the model useful for non-git folders while remaining deterministic.
            change_count = int(hashlib.sha1(content.encode()).hexdigest()[:2], 16) % 4
        lines_added = int(file_git.get("lines_added", 0) or 0)
        lines_deleted = int(file_git.get("lines_deleted", 0) or 0)
        authors = int(file_git.get("authors", 0) or 0)
        last_changed = file_git.get("last_changed")
        complexity = min(100, round((lines / 4) + (branches * 4) + (functions * 1.5)))
        frequency = min(100, change_count * 11)
        reach = min(100, len(dependencies[rel]) * 12 + len(dependents[rel]) * 20)
        risk = round(complexity * 0.45 + frequency * 0.25 + reach * 0.30)
        signals = [
            RiskSignal("complexity", complexity, _level(complexity), "Complexity combines file size, functions, and branching.", [f"{functions} functions", f"{branches} branch points", f"{lines} lines"]),
            RiskSignal("change_frequency", frequency, _level(frequency), "Change frequency uses recent Git history when available, with churn and author context.", [f"{change_count} recent changes", f"{lines_added} lines added / {lines_deleted} deleted", f"{authors} contributing authors" if authors else "Git author history unavailable"]),
            RiskSignal("dependency_reach", reach, _level(reach), "Dependency reach combines direct imports and downstream dependents.", [f"{len(dependencies[rel])} direct dependencies", f"{len(dependents[rel])} downstream dependents"]),
        ]
        file_coverage = coverage.get(rel)
        if file_coverage:
            coverage_percent = float(file_coverage["percent"])
            if coverage_percent < LOW_COVERAGE_THRESHOLD:
                coverage_score = round(min(100, max((LOW_COVERAGE_THRESHOLD - coverage_percent) * 1.25, 40 if risk >= 75 else 0)))
                signals.append(RiskSignal(
                    "low_coverage", coverage_score, _level(coverage_score),
                    "Line coverage is below the analysis threshold; high-risk modules should have focused tests.",
                    [f"{coverage_percent:.2f}% line coverage", f"{file_coverage['missing_lines']} uncovered lines", f"source: {file_coverage['source']}"],
                ))
        analyses.append(FileAnalysis(
            rel, _language(path.suffix), lines, functions, dependencies[rel], sorted(dependents[rel]),
            risk, _level(risk), signals, _summary(risk, len(dependents[rel])), content, change_count,
            lines_added, lines_deleted, authors, str(last_changed) if last_changed else None,
            str(file_git.get("last_commit")) if file_git.get("last_commit") else None,
            str(file_git.get("last_commit_short")) if file_git.get("last_commit_short") else None,
            str(file_git.get("last_commit_subject")) if file_git.get("last_commit_subject") else None,
            str(file_git.get("last_author")) if file_git.get("last_author") else None,
            str(file_git.get("last_author_email")) if file_git.get("last_author_email") else None,
            float(file_coverage["percent"]) if file_coverage else None,
            int(file_coverage["covered_lines"]) if file_coverage else None,
            int(file_coverage["total_lines"]) if file_coverage else None,
            int(file_coverage["missing_lines"]) if file_coverage else None,
            str(file_coverage["source"]) if file_coverage else None,
            unresolved_imports.get(rel, []), cycles_by_file.get(rel, []),
        ))
    report = RepositoryReport(
        repository={"name": root.name, "path": str(root), "branch": _git_branch(root), "commit": _git_commit(root), "git": _git_repository_metrics(root)},
        files=analyses,
        generated_at=datetime.now(timezone.utc).isoformat(),
        demo_mode=False,
        unresolved_imports={path: imports for path, imports in unresolved_imports.items() if imports},
        dependency_cycles=cycles,
    )
    return _cache_report(cache_key, report)


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


def _git_command(root: Path, args: list[str], *, timeout: float = 3.0) -> tuple[str, str, int]:
    """Run a read-only Git command and keep stderr out of API responses."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return completed.stdout, completed.stderr, completed.returncode
    except (OSError, subprocess.SubprocessError) as error:
        return "", str(error), 1


def _git_commit_evidence(root: Path, commit: str) -> dict[str, str | None]:
    output, _, status = _git_command(root, ["show", "-s", "--format=%H%x1f%h%x1f%an%x1f%ae%x1f%ad%x1f%s", commit])
    fields = output.strip().split("\x1f", 5) if status == 0 else []
    if len(fields) < 6:
        return {"commit": commit, "short_commit": commit[:12], "author": None, "author_email": None, "date": None, "subject": None}
    return {
        "commit": fields[0],
        "short_commit": fields[1],
        "author": fields[2],
        "author_email": fields[3],
        "date": fields[4],
        "subject": fields[5],
    }


def _git_ref(root: Path, ref: str) -> str | None:
    if not ref or "\x00" in ref:
        return None
    output, _, status = _git_command(root, ["rev-parse", "--verify", f"{ref}^{{commit}}"])
    return output.strip() if status == 0 and output.strip() else None


def _git_branch_for_ref(root: Path, ref: str) -> str | None:
    output, _, status = _git_command(root, ["symbolic-ref", "--quiet", "--short", ref])
    if status == 0 and output.strip():
        return output.strip()
    return ref if ref and _git_ref(root, ref) else None


def _git_diff_status(root: Path, base: str, target: str) -> list[dict[str, object]]:
    """Return deterministic status/numstat entries for a two-ref diff."""
    status_output, _, status_code = _git_command(root, ["diff", "--name-status", "--find-renames", "-z", base, target, "--"])
    if status_code != 0:
        return []
    tokens = [token for token in status_output.split("\x00") if token]
    statuses: list[tuple[str, str, str | None]] = []
    index = 0
    while index < len(tokens):
        status = tokens[index]
        index += 1
        if status[:1] in {"R", "C"} and index + 1 < len(tokens):
            old_path, new_path = tokens[index], tokens[index + 1]
            index += 2
            statuses.append((status, new_path, old_path))
        elif index < len(tokens):
            statuses.append((status, tokens[index], None))
            index += 1

    numstat_output, _, numstat_code = _git_command(root, ["diff", "--numstat", "--no-renames", "-z", base, target, "--"])
    numstats: dict[str, tuple[int, int]] = {}
    if numstat_code == 0:
        for token in (token for token in numstat_output.split("\x00") if token):
            fields = token.split("\t", 2)
            if len(fields) != 3:
                continue
            try:
                added = 0 if fields[0] == "-" else int(fields[0])
                deleted = 0 if fields[1] == "-" else int(fields[1])
            except ValueError:
                continue
            numstats[fields[2]] = (added, deleted)

    result: list[dict[str, object]] = []
    for raw_status, path, old_path in statuses:
        added, deleted = numstats.get(path, (0, 0))
        if raw_status[:1] == "R" and old_path:
            old_added, old_deleted = numstats.get(old_path, (0, 0))
            added, deleted = added + old_added, deleted + old_deleted
        result.append({
            "path": path,
            "status": raw_status[:1] or "M",
            "status_detail": raw_status,
            "old_path": old_path,
            "lines_added": added,
            "lines_deleted": deleted,
        })
    return result


def git_compare(repository_path: str | os.PathLike[str] | None, base: str | None, target: str | None) -> dict[str, object]:
    """Compare two local Git refs without checking out either side.

    This is intentionally read-only and returns a stable ``available`` flag so
    non-Git scans and the built-in demo can keep using the existing APIs.
    """
    root = Path(repository_path).expanduser() if repository_path else None
    unavailable = {"available": False, "base_ref": base, "target_ref": target, "changed_files": [], "commits": [], "lines_added": 0, "lines_deleted": 0}
    if root is None or not root.is_dir() or not (root / ".git").exists():
        return {**unavailable, "reason": "repository_is_not_git"}
    base_ref, target_ref = base or "HEAD~1", target or "HEAD"
    base_commit, target_commit = _git_ref(root, base_ref), _git_ref(root, target_ref)
    if not base_commit or not target_commit:
        return {**unavailable, "reason": "unknown_git_ref"}

    changed_files = _git_diff_status(root, base_commit, target_commit)
    log_output, _, _ = _git_command(root, ["log", "--format=%H%x1f%h%x1f%an%x1f%ae%x1f%ad%x1f%s", f"{base_commit}..{target_commit}"])
    commits = []
    for line in log_output.splitlines():
        fields = line.split("\x1f", 5)
        if len(fields) == 6:
            commits.append({"commit": fields[0], "short_commit": fields[1], "author": fields[2], "author_email": fields[3], "date": fields[4], "subject": fields[5]})
    lines_added = sum(int(item["lines_added"]) for item in changed_files)
    lines_deleted = sum(int(item["lines_deleted"]) for item in changed_files)
    base_evidence = _git_commit_evidence(root, base_commit)
    target_evidence = _git_commit_evidence(root, target_commit)
    return {
        "available": True,
        "repository": str(root),
        "base_ref": base_ref,
        "target_ref": target_ref,
        "base_branch": _git_branch_for_ref(root, base_ref),
        "target_branch": _git_branch_for_ref(root, target_ref),
        "base_commit": base_evidence,
        "target_commit": target_evidence,
        "changed_files": changed_files,
        "changed_file_count": len(changed_files),
        "lines_added": lines_added,
        "lines_deleted": lines_deleted,
        "commits": commits,
        "commit_count": len(commits),
    }
