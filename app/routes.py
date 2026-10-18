"""HTTP routes for the RepoPilot prototype and its backend view models."""

from __future__ import annotations

from collections import deque
from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from flask import Blueprint, Response, current_app, jsonify, render_template, request, send_file

from .history import DEFAULT_WEIGHTS, SnapshotStore
from .scanner import git_compare

api = Blueprint("api", __name__)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPERIMENTS_DIR = PROJECT_ROOT / "reports" / "experiments"


def _store() -> SnapshotStore:
    return current_app.extensions["repopilot_store"]


def _report():
    return _store().report


def _payload(report=None, view: str = "dashboard"):
    report = report or _report()
    # Weighted report copies and legacy snapshots may only retain structural
    # metadata on each file.  Reconstruct the aggregate fields so every API
    # representation exposes the same graph/findings facts.
    if not report.unresolved_imports:
        report.unresolved_imports = {
            item.path: list(item.unresolved_imports) for item in report.files if item.unresolved_imports
        }
    if not report.dependency_cycles:
        report.dependency_cycles = [
            list(cycle)
            for cycle in sorted({tuple(cycle) for item in report.files for cycle in item.dependency_cycles})
        ]
    body = report.to_dict()
    body["view"] = view
    return body


def _experiments() -> dict[str, Any]:
    """Load generated experiment evidence without making it required at runtime."""
    path = EXPERIMENTS_DIR / "experiments.json"
    if not path.is_file():
        return {"available": False, "message": "Run python experiments/run_experiments.py to generate evidence."}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"available": False, "message": "Experiment evidence could not be read."}
    if not isinstance(payload, dict):
        return {"available": False, "message": "Experiment evidence has an invalid format."}
    payload.update({"available": True, "json_url": "/reports/experiments/experiments.json", "markdown_url": "/reports/experiments/experiments.md"})
    return payload


def _wants_json() -> bool:
    """Keep the JSON API convenient while serving HTML to real browsers."""
    if request.path.startswith("/api/"):
        return True
    accepted = request.accept_mimetypes
    if not accepted:
        return True
    return accepted.best == "application/json" and accepted["application/json"] >= accepted["text/html"]


def _context(report=None, view: str = "dashboard") -> dict:
    body = _payload(report, view)
    summary = body.get("summary", {})
    files = body.get("files", [])
    signals = [signal for item in files for signal in item.get("signals", [])]
    averages = {}
    for name in ("complexity", "change_frequency", "dependency_reach"):
        values = [signal["score"] for signal in signals if signal["name"] == name]
        averages[name] = round(sum(values) / len(values)) if values else 0
    summary.update({"complexity": averages["complexity"], "change_frequency": averages["change_frequency"], "dependency_reach": averages["dependency_reach"]})
    summary["dependency_count"] = sum(len(item.get("dependencies", [])) for item in files)
    summary["nodes"] = len(files)
    summary["edges"] = sum(len(item.get("dependencies", [])) for item in files)
    body["summary"] = summary
    body.update({"report": body, "repo": body.get("repository", {}), "files": files, "hotspots": summary.get("hotspots", []), "metrics": summary, "demo_mode": body.get("demo_mode", False), "generated_at": "just now"})
    body.update({"history": _history_body(), "findings": _findings(_report())[:8], "weights": dict(_store().weights), "graph": _graph(_report()), "file_options": [item.to_dict() for item in _report().files], "experiments": _experiments(), "experiments_json_url": "/reports/experiments/experiments.json", "experiments_markdown_url": "/reports/experiments/experiments.md"})
    return body


def _requested_path() -> str | None:
    data = request.get_json(silent=True) or request.form
    return data.get("repo_path") or request.args.get("repo_path") or request.args.get("path")


def _git_refs(*, include_generic: bool = False) -> tuple[str | None, str | None]:
    """Read additive Git comparison parameters without changing old selectors."""
    data = request.get_json(silent=True) or request.form
    base = data.get("base_commit") or data.get("base_branch") or data.get("base_ref") or request.args.get("base_commit") or request.args.get("base_branch") or request.args.get("base_ref")
    target = data.get("target_commit") or data.get("target_branch") or data.get("target_ref") or request.args.get("target_commit") or request.args.get("target_branch") or request.args.get("target_ref")
    if include_generic:
        base = base or data.get("base") or request.args.get("base")
        target = target or data.get("target") or request.args.get("target")
    return base, target


def _git_comparison(base_report=None, target_report=None, base_ref: str | None = None, target_ref: str | None = None) -> dict[str, Any]:
    target_report = target_report or _report()
    repository = target_report.repository
    root = repository.get("path")
    if not base_ref:
        base_ref = (base_report or target_report).repository.get("commit") if base_report else None
    if not target_ref:
        target_ref = target_report.repository.get("commit")
    if not base_ref or not target_ref:
        return {"available": False, "reason": "comparison_refs_unavailable", "changed_files": [], "commits": [], "lines_added": 0, "lines_deleted": 0}
    return git_compare(root, base_ref, target_ref)


def _merge_git_changes(changed: list[dict[str, Any]], git_diff: dict[str, Any]) -> list[dict[str, Any]]:
    """Attach Git status/numstat evidence while retaining snapshot fields."""
    by_path = {item.get("path"): item for item in changed}
    for diff in git_diff.get("changed_files", []):
        path = diff.get("path")
        item = by_path.get(path)
        if item is None:
            item = {"path": path, "status": "changed", "before": None, "after": None}
            changed.append(item)
            by_path[path] = item
        item["git_status"] = diff.get("status")
        item["git_status_detail"] = diff.get("status_detail")
        item["old_path"] = diff.get("old_path")
        item["lines_added"] = diff.get("lines_added", 0)
        item["lines_deleted"] = diff.get("lines_deleted", 0)
    return sorted(changed, key=lambda item: item.get("path", ""))


def _file(file_path: str | None):
    normalized = (file_path or "").replace("\\", "/").lstrip("/")
    return next((entry for entry in _report().files if entry.path == normalized), None)


def _not_found(file_path: str):
    return jsonify({"error": "file_not_found", "message": f"No analyzed file named '{file_path}'."}), 404


@api.get("/")
@api.get("/dashboard")
def dashboard():
    if _wants_json():
        return jsonify(_payload(view="dashboard"))
    return render_template("dashboard.html", **_context(view="dashboard"))


@api.route("/scan", methods=["GET", "POST"])
def scan():
    if request.method == "GET":
        body = {"view": "scan", "fields": [{"name": "repo_path", "label": "Repository path", "placeholder": "C:/projects/my-repo", "required": False}], "demo_available": True, "current_repository": _report().repository}
        if _wants_json():
            return jsonify(body)
        return render_template("scan.html", repo=_report().repository, scan=body, generated_at="just now")
    _store().scan(_requested_path())
    if _wants_json() or request.is_json:
        return jsonify(_payload(view="dashboard")), 200
    return render_template("dashboard.html", **_context(view="dashboard")), 200


@api.get("/file/<path:file_path>")
@api.get("/api/files/<path:file_path>")
def file_detail(file_path: str):
    item = _file(file_path)
    if item is None:
        if _wants_json():
            return _not_found(file_path)
        return render_template("file_detail.html", repo=_report().repository, selected_file={}, generated_at="just now"), 404
    findings = [finding for finding in _findings(_report()) if finding["file"] == item.path]
    body = {
        "view": "file_detail",
        "repository": _report().repository,
        "file": item.to_dict(include_content=True),
        "findings": findings,
        "structural_findings": [finding for finding in findings if finding["signal"] in {"unresolved_imports", "dependency_cycles"}],
    }
    if _wants_json():
        return jsonify(body)
    return render_template("file_detail.html", **body, repo=_report().repository, selected_file=body["file"], generated_at="just now")


@api.get("/compare")
def compare():
    left_path = request.args.get("base") or request.args.get("left")
    right_path = request.args.get("target") or request.args.get("right")
    report = _report()
    left = next((entry for entry in report.files if entry.path == left_path), report.files[0] if report.files else None)
    right = next((entry for entry in report.files if entry.path == right_path), report.files[1] if len(report.files) > 1 else left)
    if left is None or right is None:
        return jsonify({"error": "no_files", "message": "Scan a repository containing at least one supported file first."}), 404
    body = {"view": "compare", "repository": report.repository, "base": left.to_dict(), "target": right.to_dict(), "delta": {"risk_score": right.risk_score - left.risk_score, "lines": right.lines - left.lines, "dependencies": len(right.dependencies) - len(left.dependencies)}}
    if _wants_json():
        return jsonify(body)
    context = _context(view="compare")
    context.update({"comparison": body, "base": body["base"], "target": body["target"], "delta": body["delta"]})
    return render_template("comparison.html", **context)


@api.get("/api/report")
def report():
    include_content = request.args.get("include_content", "false").lower() in {"1", "true", "yes"}
    body = _report().to_dict(include_content=include_content)
    body["view"] = "report"
    return jsonify(body)


@api.get("/health")
def health():
    return jsonify({"status": "ok", "service": "repopilot", "demo_mode": _report().demo_mode})


@api.get("/api/experiments")
@api.get("/experiments")
def experiments():
    evidence = _experiments()
    if _wants_json():
        return jsonify({"view": "experiments", **evidence})
    context = _context(view="experiments")
    context.update({"experiments": evidence, "experiments_json_url": evidence.get("json_url", "/reports/experiments/experiments.json"), "experiments_markdown_url": evidence.get("markdown_url", "/reports/experiments/experiments.md")})
    return render_template("experiments.html", **context)


@api.get("/reports/experiments/experiments.json")
def experiments_json_download():
    path = EXPERIMENTS_DIR / "experiments.json"
    if not path.is_file():
        return jsonify({"error": "experiments_not_found", "message": "Run the experiment runner first."}), 404
    return send_file(path, mimetype="application/json", as_attachment=True, download_name="repopilot-experiments.json")


@api.get("/reports/experiments/experiments.md")
def experiments_markdown_download():
    path = EXPERIMENTS_DIR / "experiments.md"
    if not path.is_file():
        return jsonify({"error": "experiments_not_found", "message": "Run the experiment runner first."}), 404
    return send_file(path, mimetype="text/markdown", as_attachment=True, download_name="repopilot-experiments.md")


@api.get("/api/git/activity")
@api.get("/git/activity")
def git_activity():
    """Expose the file-level Git evidence used by the change signal."""
    report = _report()
    files = sorted(
        (
            {
                "path": item.path,
                "change_count": item.change_count,
                "lines_added": item.lines_added,
                "lines_deleted": item.lines_deleted,
                "authors": item.authors,
                "last_changed": item.last_changed,
                "last_commit": item.last_commit,
                "last_commit_short": item.last_commit_short,
                "last_commit_subject": item.last_commit_subject,
                "last_author": item.last_author,
                "last_author_email": item.last_author_email,
            }
            for item in report.files
        ),
        key=lambda item: (-item["change_count"], -item["lines_added"] - item["lines_deleted"], item["path"]),
    )
    return jsonify({"view": "git_activity", "repository": report.repository, "window_days": report.repository.get("git", {}).get("window_days", 90), "files": files})


@api.get("/api/git/compare")
@api.get("/git/compare")
def git_compare_query():
    """Return a raw local Git diff independently of snapshot comparison."""
    base_ref, target_ref = _git_refs(include_generic=True)
    diff = git_compare(_report().repository.get("path"), base_ref, target_ref)
    return jsonify({"view": "git_compare", "repository": _report().repository, **diff})


def _history_body() -> dict[str, Any]:
    snapshots = _store().snapshots
    timeline = [item.metadata() for item in snapshots]
    return {"view": "history", "repository": _report().repository, "current_snapshot_id": _store().current_snapshot_id, "snapshots": timeline, "timeline": timeline}


@api.get("/api/snapshots")
@api.get("/api/snapshots/timeline")
@api.get("/snapshots")
@api.get("/snapshots/timeline")
@api.get("/api/history")
@api.get("/history")
@api.get("/api/snapshot-history")
@api.get("/snapshot-history")
@api.get("/api/timeline")
@api.get("/timeline")
def history():
    body = _history_body()
    if _wants_json():
        return jsonify(body)
    context = _context(view="history")
    context.update({"history": body, "snapshots": body["snapshots"]})
    return render_template("history.html", **context)


@api.get("/api/snapshots/<snapshot_id>")
def snapshot_detail(snapshot_id: str):
    item = _store().snapshot(snapshot_id)
    if item is None:
        return jsonify({"error": "snapshot_not_found", "message": f"No snapshot named '{snapshot_id}'."}), 404
    body = item.report.to_dict()
    body.update({"view": "snapshot", "snapshot_id": item.id, "weights": item.weights})
    return jsonify(body)


@api.post("/api/snapshots/<snapshot_id>/select")
def select_snapshot(snapshot_id: str):
    item = _store().select_snapshot(snapshot_id)
    if item is None:
        return jsonify({"error": "snapshot_not_found", "message": f"No snapshot named '{snapshot_id}'."}), 404
    return jsonify({"view": "snapshot_selected", "snapshot_id": item.id, "repository": item.report.repository})


def _repository_summary(report) -> dict[str, Any]:
    body = report.to_dict()
    return {"file_count": body["summary"]["file_count"], "average_risk": body["summary"]["average_risk"], "risk_counts": body["summary"]["risk_counts"], "dependency_count": sum(len(item.dependencies) for item in report.files)}


@api.get("/api/compare/repository")
@api.get("/api/repository/compare")
@api.get("/compare/repository")
@api.get("/repository/compare")
def repository_compare():
    snapshots = _store().snapshots
    requested_base_ref, requested_target_ref = _git_refs()
    base_id = request.args.get("base_snapshot") or request.args.get("base") or request.args.get("before")
    target_id = request.args.get("target_snapshot") or request.args.get("target") or request.args.get("after")
    base = _store().snapshot(base_id) if base_id else (snapshots[-2] if len(snapshots) > 1 else snapshots[-1] if snapshots else None)
    target = _store().snapshot(target_id) if target_id else (snapshots[-1] if snapshots else None)
    # ``base``/``target`` remain snapshot selectors.  If they are not snapshot
    # IDs, accept them as Git refs for callers that want a direct branch/commit
    # comparison without creating two persisted scans first.
    git_base_ref = requested_base_ref or (base_id if base_id and base is None else None)
    git_target_ref = requested_target_ref or (target_id if target_id and target is None else None)
    git_only = bool(requested_base_ref or requested_target_ref) or ((base is None or target is None) and bool(git_base_ref or git_target_ref))
    if git_only:
        target = target or _store().snapshot(None)
        base = base or target
    if base is None or target is None:
        return jsonify({"error": "no_snapshots", "message": "No repository snapshots are available."}), 404
    before = _repository_summary(base.report)
    after = _repository_summary(target.report)
    before_paths = {item.path: item for item in base.report.files}
    after_paths = {item.path: item for item in target.report.files}
    changed = []
    if not git_only:
        for path in sorted(set(before_paths) | set(after_paths)):
            left, right = before_paths.get(path), after_paths.get(path)
            if left is None or right is None or left.risk_score != right.risk_score or left.lines != right.lines or left.dependencies != right.dependencies:
                changed.append({"path": path, "status": "added" if left is None else "removed" if right is None else "changed", "before": left.to_dict() if left else None, "after": right.to_dict() if right else None})
    git_diff = _git_comparison(
        base.report,
        target.report,
        git_base_ref or (base.report.repository.get("commit") if base.report.repository.get("commit") != target.report.repository.get("commit") else None),
        git_target_ref or target.report.repository.get("commit"),
    )
    if git_only and not git_diff.get("available"):
        return jsonify({"error": "git_compare_unavailable", "message": git_diff.get("reason", "The requested Git refs could not be compared."), "git": git_diff}), 404
    if git_only:
        before_repository = dict(base.report.repository)
        after_repository = dict(target.report.repository)
        before_repository.update({"branch": git_diff.get("base_branch") or git_base_ref, "commit": (git_diff.get("base_commit") or {}).get("short_commit", git_base_ref)})
        after_repository.update({"branch": git_diff.get("target_branch") or git_target_ref, "commit": (git_diff.get("target_commit") or {}).get("short_commit", git_target_ref)})
    else:
        before_repository, after_repository = base.report.repository, target.report.repository
    before_view = {"snapshot_id": None if git_only else base.id, "repository": before_repository, "summary": before}
    after_view = {"snapshot_id": None if git_only else target.id, "repository": after_repository, "summary": after}
    finding_changes = _finding_lifecycle(target.report, base.report, target.id, base.id)
    changed = _merge_git_changes(changed, git_diff)
    delta = {key: after[key] - before[key] for key in ("file_count", "average_risk", "dependency_count")}
    delta.update({
        "changed_file_count": git_diff.get("changed_file_count", 0),
        "lines_added": git_diff.get("lines_added", 0),
        "lines_deleted": git_diff.get("lines_deleted", 0),
        "commit_count": git_diff.get("commit_count", 0),
    })
    body = {
        "view": "repository_compare", "base": before_view, "target": after_view, "before": before_view, "after": after_view,
        "delta": delta,
        "changed_files": changed, "finding_changes": _finding_change_summary(finding_changes),
        "git": git_diff, "git_diff": git_diff, "diff": git_diff,
    }
    if _wants_json():
        return jsonify(body)
    context = _context(view="repository_compare")
    context.update({"comparison": body, "base_snapshot": before_view, "target_snapshot": after_view, "changed_files": changed})
    return render_template("repository_compare.html", **context)


def _findings(report) -> list[dict[str, Any]]:
    findings = []
    for item in report.files:
        for signal in item.signals:
            if signal.score < 40:
                continue
            findings.append({"id": f"{item.path}:{signal.name}", "file": item.path, "signal": signal.name, "score": signal.score, "severity": signal.level, "title": f"{signal.name.replace('_', ' ').title()} risk in {item.path}", "explanation": signal.explanation, "evidence": list(signal.evidence), "recommendation": f"Review {signal.name.replace('_', ' ')} before changing this file."})
        if item.unresolved_imports:
            imports = list(item.unresolved_imports)
            score = min(100, 65 + (len(imports) * 10))
            findings.append({
                "id": f"{item.path}:unresolved_imports",
                "file": item.path,
                "signal": "unresolved_imports",
                "score": score,
                "severity": "critical" if score >= 90 else "high" if score >= 75 else "medium",
                "title": f"Unresolved imports in {item.path}",
                "explanation": "The scanner found imports that do not resolve to files in the repository.",
                "evidence": [f"Unresolved import: {name}" for name in imports],
                "recommendation": "Resolve these imports or document the external dependency before changing this file.",
            })
        if item.dependency_cycles:
            cycles = [list(cycle) for cycle in item.dependency_cycles]
            score = min(100, 80 + (len(cycles) * 10))
            findings.append({
                "id": f"{item.path}:dependency_cycles",
                "file": item.path,
                "signal": "dependency_cycles",
                "score": score,
                "severity": "critical" if score >= 90 else "high",
                "title": f"Circular dependency involving {item.path}",
                "explanation": "The file participates in one or more circular dependency components.",
                "evidence": ["Circular dependency: " + " -> ".join(cycle + [cycle[0]]) for cycle in cycles],
                "recommendation": "Break the cycle by extracting a lower-level module or inverting the dependency.",
            })
        signals = {signal.name: signal for signal in item.signals}
        high_risk = item.risk_level in {"high", "critical"} or item.risk_score >= 75
        low_coverage = signals.get("low_coverage")
        if high_risk and low_coverage is not None:
            score = min(100, max(item.risk_score, low_coverage.score) + 10)
            findings.append({"id": f"{item.path}:high_risk_low_coverage", "file": item.path, "signal": "high_risk_low_coverage", "score": score, "severity": "critical" if score >= 90 else "high", "title": f"High risk with low coverage in {item.path}", "explanation": "This high-risk module has measured line coverage below the analysis threshold.", "evidence": [f"risk score: {item.risk_score}", *low_coverage.evidence], "recommendation": "Add focused tests for uncovered paths before changing this high-risk module."})
        change_signal = signals.get("change_frequency")
        if high_risk and item.change_count >= 2 and change_signal is not None:
            score = min(100, max(item.risk_score, change_signal.score) + 10)
            findings.append({"id": f"{item.path}:high_risk_high_churn", "file": item.path, "signal": "high_risk_high_churn", "score": score, "severity": "critical" if score >= 90 else "high", "title": f"High risk with high churn in {item.path}", "explanation": "This high-risk module has changed repeatedly in the recent history window.", "evidence": [f"risk score: {item.risk_score}", *change_signal.evidence], "recommendation": "Review recent changes and add regression coverage before further edits."})
    return sorted(findings, key=lambda entry: (-entry["score"], entry["file"], entry["signal"]))


def _finding_lifecycle(current_report, previous_report=None, current_snapshot_id: str | None = None, previous_snapshot_id: str | None = None) -> list[dict[str, Any]]:
    """Classify findings against the immediately previous snapshot."""
    current = {item["id"]: item for item in _findings(current_report)}
    previous = {item["id"]: item for item in _findings(previous_report)} if previous_report else {}
    lifecycle: list[dict[str, Any]] = []
    for finding_id, item in current.items():
        before = previous.get(finding_id)
        delta = item["score"] - before["score"] if before else item["score"]
        status = "new" if before is None else "regressed" if delta >= 3 else "improved" if delta <= -3 else "open"
        lifecycle.append({**item, "status": status, "score_delta": delta, "previous_score": before["score"] if before else None, "first_seen_snapshot": current_snapshot_id if before is None else previous_snapshot_id, "resolved_in_snapshot": None})
    for finding_id, before in previous.items():
        if finding_id not in current:
            lifecycle.append({**before, "status": "resolved", "score_delta": -before["score"], "previous_score": before["score"], "first_seen_snapshot": previous_snapshot_id, "resolved_in_snapshot": current_snapshot_id})
    return sorted(lifecycle, key=lambda entry: (0 if entry["status"] in {"new", "regressed"} else 1, -entry["score"], entry["file"], entry["signal"]))


def _finding_change_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {status: sum(1 for item in items if item["status"] == status) for status in ("new", "resolved", "regressed", "improved", "open")}
    return {"counts": counts, "findings": items}


@api.get("/api/findings")
@api.get("/findings")
def findings():
    current_snapshot = _store().snapshot(None)
    previous_snapshot = _store().previous_snapshot(current_snapshot.id if current_snapshot else None)
    result = _finding_lifecycle(_report(), previous_snapshot.report if previous_snapshot else None, current_snapshot.id if current_snapshot else None, previous_snapshot.id if previous_snapshot else None)
    severity = request.args.get("severity")
    file_path = request.args.get("file") or request.args.get("path")
    if severity:
        result = [item for item in result if item["severity"] == severity.lower()]
    if file_path:
        result = [item for item in result if item["file"] == file_path]
    body = {"view": "findings", "repository": _report().repository, "count": len(result), "findings": result}
    if _wants_json():
        return jsonify(body)
    context = _context(view="findings")
    context.update({"findings": result, "finding_count": len(result)})
    return render_template("findings.html", **context)


@api.get("/api/findings/compare")
def findings_compare():
    snapshots = _store().snapshots
    base_id = request.args.get("base_snapshot") or request.args.get("base") or request.args.get("before")
    target_id = request.args.get("target_snapshot") or request.args.get("target") or request.args.get("after")
    base = _store().snapshot(base_id) if base_id else (snapshots[-2] if len(snapshots) > 1 else None)
    target = _store().snapshot(target_id) if target_id else (snapshots[-1] if snapshots else None)
    if base is None or target is None:
        return jsonify({"error": "no_snapshots", "message": "No repository snapshots are available."}), 404
    items = _finding_lifecycle(target.report, base.report, target.id, base.id)
    return jsonify({"view": "finding_compare", "base_snapshot_id": base.id, "target_snapshot_id": target.id, **_finding_change_summary(items)})


def _impact(file_path: str):
    item = _file(file_path)
    if item is None:
        return None
    report = _report()
    by_path = {entry.path: entry for entry in report.files}
    related: set[str] = set()
    queue = deque(item.dependencies + item.dependents)
    while queue:
        path = queue.popleft()
        if path in related or path not in by_path or path == item.path:
            continue
        related.add(path)
        entry = by_path[path]
        queue.extend(entry.dependencies)
        queue.extend(entry.dependents)
    affected = [by_path[path] for path in sorted(related)]
    direct_paths = set(item.dependencies + item.dependents)
    direct = [by_path[path] for path in sorted(direct_paths) if path in by_path]

    def risk_summary(entries: list[Any]) -> dict[str, Any]:
        if not entries:
            return {"score": 0, "level": "low", "count": 0, "high_risk_count": 0}
        highest = max(entries, key=lambda entry: entry.risk_score)
        return {
            "score": highest.risk_score,
            "level": highest.risk_level,
            "count": len(entries),
            "high_risk_count": sum(entry.risk_level in {"high", "critical"} for entry in entries),
        }

    def evidence_for(entry: Any) -> dict[str, Any]:
        return {
            "file": entry.path,
            "risk_score": entry.risk_score,
            "risk_level": entry.risk_level,
            "signals": [
                {
                    "name": signal.name,
                    "score": signal.score,
                    "level": signal.level,
                    "evidence": list(signal.evidence),
                }
                for signal in entry.signals
            ],
        }

    warnings: list[dict[str, Any]] = []
    for entry in [item, *affected]:
        if entry.unresolved_imports:
            warnings.append({
                "type": "unresolved_imports",
                "file": entry.path,
                "items": list(entry.unresolved_imports),
                "message": f"{len(entry.unresolved_imports)} import(s) do not resolve locally.",
            })
        if entry.dependency_cycles:
            warnings.append({
                "type": "dependency_cycles",
                "file": entry.path,
                "cycles": [list(cycle) for cycle in entry.dependency_cycles],
                "message": "This file participates in a circular dependency.",
            })

    affected_details = [entry.to_dict() for entry in affected]
    dependency_entries = [by_path[path] for path in item.dependencies if path in by_path]
    dependent_entries = [by_path[path] for path in item.dependents if path in by_path]
    direct_details = [entry.to_dict() for entry in dependency_entries]
    base_ref, target_ref = _git_refs(include_generic=True)
    git_diff = _git_comparison(base_ref=base_ref, target_ref=target_ref)
    file_diff = next((entry for entry in git_diff.get("changed_files", []) if entry.get("path") == item.path or entry.get("old_path") == item.path), None)
    return {
        "file": item.to_dict(),
        # Keep the original path arrays stable for consumers that render links.
        "direct_dependencies": list(item.dependencies),
        "direct_dependents": list(item.dependents),
        "affected_files": [entry.path for entry in affected],
        "affected_count": len(affected),
        "direct_dependency_details": direct_details,
        "direct_dependent_details": [entry.to_dict() for entry in sorted((by_path[path] for path in item.dependents if path in by_path), key=lambda entry: entry.path)],
        "affected_file_details": affected_details,
        "direct_risk": risk_summary(direct),
        "direct_dependency_risk": risk_summary(dependency_entries),
        "direct_dependent_risk": risk_summary(dependent_entries),
        "affected_risk": risk_summary(affected),
        "risk_score": item.risk_score,
        "estimated_change_surface": "high" if len(affected) >= 3 or item.risk_score >= 75 else "medium" if affected else "low",
        "evidence": [evidence_for(entry) for entry in [item, *affected]],
        "warnings": warnings,
        "git": item.to_dict().get("git", {}),
        "repository_git": dict(report.repository.get("git", {})),
        "git_context": {
            "branch": report.repository.get("branch"),
            "commit": report.repository.get("commit"),
            "file": item.to_dict().get("git", {}),
            "comparison": git_diff,
            "diff": file_diff,
        },
        "git_diff": file_diff,
        "diff": file_diff,
    }


@api.get("/api/impact")
@api.get("/api/change-impact")
@api.get("/impact")
@api.get("/change-impact")
def impact_query():
    file_path = request.args.get("file") or request.args.get("path")
    result = _impact(file_path or "")
    if not _wants_json():
        context = _context(view="impact")
        context.update({"impact": result, "selected_path": file_path or (context["files"][0]["path"] if context["files"] else "")})
        return render_template("impact.html", **context), 200 if result else 404
    return jsonify(result or {"error": "file_not_found", "message": f"No analyzed file named '{file_path}'."}), 200 if result else 404


@api.get("/api/impact/<path:file_path>")
@api.get("/api/change-impact/<path:file_path>")
@api.get("/impact/<path:file_path>")
def impact_path(file_path: str):
    result = _impact(file_path)
    return jsonify(result or {"error": "file_not_found", "message": f"No analyzed file named '{file_path}'."}), 200 if result else 404


def _graph(report) -> dict[str, Any]:
    # Keep the graph view model independent of the decorative CSS layout used
    # by the first version of the page.  Coordinates are calculated from
    # sorted paths and the resolved dependency edges, so a report loaded from
    # SQLite (or constructed with a different file iteration order) produces
    # the same geometry.
    graph_files = list(report.files)
    node_ids = {item.path for item in graph_files}
    raw_edges = [
        {"source": item.path, "target": dependency}
        for item in graph_files
        for dependency in item.dependencies
    ]
    edge_pairs = sorted({(edge["source"], edge["target"]) for edge in raw_edges})

    node_width = 220
    node_height = 76
    padding = 32
    horizontal_gap = 80
    vertical_gap = 48

    # A small, dependency-free layered layout.  Kahn's ordering gives
    # dependency-connected nodes useful columns; cyclic components are placed
    # deterministically in the remaining column rather than looping forever.
    incoming = {path: 0 for path in node_ids}
    adjacency: dict[str, list[str]] = {path: [] for path in node_ids}
    for source, target in edge_pairs:
        if source not in node_ids or target not in node_ids:
            continue
        incoming[target] += 1
        adjacency[source].append(target)
    for targets in adjacency.values():
        targets.sort()
    ranks = {path: 0 for path in node_ids}
    queue = sorted(path for path, count in incoming.items() if count == 0)
    processed: set[str] = set()
    while queue:
        source = queue.pop(0)
        if source in processed:
            continue
        processed.add(source)
        for target in adjacency[source]:
            ranks[target] = max(ranks[target], ranks[source] + 1)
            incoming[target] -= 1
            if incoming[target] == 0:
                queue.append(target)
                queue.sort()

    # Nodes in a cycle never reach the zero-incoming queue.  They still get a
    # stable placement, and any rank reached from an acyclic predecessor is
    # retained above.
    for path in sorted(node_ids - processed):
        ranks[path] = max(ranks[path], 0)

    columns: dict[int, list[str]] = {}
    for path in sorted(node_ids):
        columns.setdefault(ranks[path], []).append(path)
    positions: dict[str, dict[str, int]] = {}
    for rank in sorted(columns):
        for row, path in enumerate(columns[rank]):
            positions[path] = {
                "x": padding + rank * (node_width + horizontal_gap),
                "y": padding + row * (node_height + vertical_gap),
            }

    column_count = max(columns, default=0) + 1
    row_count = max((len(paths) for paths in columns.values()), default=0)
    canvas_width = max(960, padding * 2 + column_count * node_width + max(0, column_count - 1) * horizontal_gap)
    canvas_height = max(360, padding * 2 + row_count * node_height + max(0, row_count - 1) * vertical_gap)

    unresolved_by_file = {
        path: list(imports) for path, imports in report.unresolved_imports.items() if imports
    }
    # Weighted/reloaded reports from older databases may not carry the
    # repository-level maps, while the per-file metadata is durable.  Rebuild
    # the graph annotations from that metadata for a consistent API response.
    for item in report.files:
        if item.unresolved_imports:
            unresolved_by_file.setdefault(item.path, list(item.unresolved_imports))
    cycles = [list(cycle) for cycle in report.dependency_cycles]
    if not cycles:
        cycles = sorted({tuple(cycle) for item in report.files for cycle in item.dependency_cycles})
        cycles = [list(cycle) for cycle in cycles]
    unresolved = [
        {"file": path, "imports": list(imports)}
        for path, imports in sorted(unresolved_by_file.items()) if imports
    ]

    cycle_sets = [set(cycle) for cycle in cycles]

    def cycle_for(source: str, target: str) -> list[str] | None:
        return next((sorted(component) for component in cycle_sets if source in component and target in component), None)

    def edge_path(source: str, target: str) -> str | None:
        source_position = positions.get(source)
        target_position = positions.get(target)
        if source_position is None or target_position is None:
            return None
        source_x, source_y = source_position["x"], source_position["y"]
        target_x, target_y = target_position["x"], target_position["y"]
        if source == target:
            center_y = source_y + node_height // 2
            right = source_x + node_width
            return f"M {right} {center_y} C {right + 40} {source_y - 12}, {right + 40} {source_y + node_height + 12}, {right} {center_y + 1}"
        if target_x > source_x:
            start = (source_x + node_width, source_y + node_height // 2)
            finish = (target_x, target_y + node_height // 2)
        elif target_x < source_x:
            start = (source_x, source_y + node_height // 2)
            finish = (target_x + node_width, target_y + node_height // 2)
        elif target_y >= source_y:
            start = (source_x + node_width // 2, source_y + node_height)
            finish = (target_x + node_width // 2, target_y)
        else:
            start = (source_x + node_width // 2, source_y)
            finish = (target_x + node_width // 2, target_y + node_height)
        return f"M {start[0]} {start[1]} L {finish[0]} {finish[1]}"

    nodes = []
    for item in graph_files:
        node_cycles = [list(cycle) for cycle in item.dependency_cycles]
        if not node_cycles:
            node_cycles = [list(cycle) for cycle in cycles if item.path in cycle]
        cycle = bool(node_cycles)
        unresolved_imports = list(item.unresolved_imports or unresolved_by_file.get(item.path, []))
        position = positions[item.path]
        nodes.append({
            "id": item.path,
            "label": item.path,
            "short_label": Path(item.path).name,
            "language": item.language,
            "risk_score": item.risk_score,
            "risk_level": item.risk_level,
            "unresolved_imports": unresolved_imports,
            "cycles": node_cycles,
            "x": position["x"],
            "y": position["y"],
            "width": node_width,
            "height": node_height,
            "position": dict(position),
            "dimensions": {"width": node_width, "height": node_height},
            "is_cycle": cycle,
            "is_unresolved": bool(unresolved_imports),
            "cycle": cycle,
            "unresolved": bool(unresolved_imports),
        })

    edges = []
    for edge in raw_edges:
        source, target = edge["source"], edge["target"]
        cycle = cycle_for(source, target)
        unresolved_edge = target not in node_ids
        metadata = {
            "relationship": "static_import",
            "cycle": bool(cycle),
            "unresolved": unresolved_edge,
        }
        edges.append({
            "source": source,
            "target": target,
            "label": "static import",
            "path": edge_path(source, target),
            "is_cycle": bool(cycle),
            "is_unresolved": unresolved_edge,
            "cycle": bool(cycle),
            "unresolved": unresolved_edge,
            "cycle_members": cycle or [],
            "metadata": metadata,
        })
    return {
        "view": "dependency_graph",
        "repository": report.repository,
        "nodes": nodes,
        "edges": edges,
        "unresolved_imports": unresolved,
        "cycles": cycles,
        "layout": {
            "algorithm": "deterministic-layered",
            "width": canvas_width,
            "height": canvas_height,
            "node_width": node_width,
            "node_height": node_height,
            "padding": padding,
            "horizontal_gap": horizontal_gap,
            "vertical_gap": vertical_gap,
            "dimensions": {"width": canvas_width, "height": canvas_height},
        },
    }


@api.get("/api/dependencies")
@api.get("/api/dependency-graph")
@api.get("/dependencies")
def dependency_graph():
    body = _graph(_report())
    if _wants_json():
        return jsonify(body)
    context = _context(view="dependencies")
    context.update({"graph": body})
    return render_template("dependencies.html", **context)


def _weight_values(source) -> tuple[dict[str, float] | None, str | None]:
    values = {}
    for name in DEFAULT_WEIGHTS:
        raw = source.get(name)
        if raw is None:
            raw = source.get(f"weight_{name}")
        if raw is not None and raw != "":
            try:
                value = float(raw)
            except (TypeError, ValueError):
                return None, f"Weight '{name}' must be numeric."
            if value < 0:
                return None, f"Weight '{name}' must be non-negative."
            values[name] = value
    if not values:
        return dict(_store().weights), None
    result = dict(_store().weights)
    result.update(values)
    total = sum(result.values())
    if total <= 0:
        return None, "At least one scoring weight must be greater than zero."
    return {name: round(result[name] / total, 6) for name in DEFAULT_WEIGHTS}, None


@api.route("/api/scoring/weights", methods=["GET", "POST"])
@api.route("/scoring/weights", methods=["GET", "POST"])
def scoring_weights():
    has_query_weights = any(key in request.args for name in DEFAULT_WEIGHTS for key in (name, f"weight_{name}"))
    if request.method == "POST" or has_query_weights:
        source = request.get_json(silent=True) or request.form or request.args
        weights, error = _weight_values(source)
        if error:
            return jsonify({"error": "invalid_weights", "message": error}), 400
        _store().set_weights(weights or dict(DEFAULT_WEIGHTS))
    return jsonify({"view": "scoring_weights", "weights": dict(_store().weights), "report": _payload(view="dashboard")})


@api.get("/settings/scoring")
def scoring_settings():
    context = _context(view="settings")
    return render_template("settings.html", **context)


def _markdown_report(report) -> str:
    summary = report.to_dict()["summary"]
    lines = [f"# RepoPilot report: {report.repository.get('name', 'repository')}", "", f"- Generated: {report.generated_at}", f"- Branch: {report.repository.get('branch', 'unknown')}", f"- Files: {summary['file_count']}", f"- Average risk: {summary['average_risk']}", "", "## Findings", ""]
    for finding in _findings(report):
        lines.append(f"- **{finding['severity'].title()} - {finding['file']} ({finding['signal']}, {finding['score']})**: {finding['explanation']}")
    lines.extend(["", "## Files", "", "| File | Risk | Level | Lines | Dependencies |", "| --- | ---: | --- | ---: | ---: |"])
    for item in report.files:
        lines.append(f"| `{item.path}` | {item.risk_score} | {item.risk_level} | {item.lines} | {len(item.dependencies)} |")
    return "\n".join(lines) + "\n"


@api.get("/api/report/markdown")
@api.get("/report/markdown")
@api.get("/report.md")
def markdown_report():
    rendered = _markdown_report(_report()).encode("utf-8", errors="strict")
    response = Response(rendered, mimetype="text/markdown")
    response.headers["Content-Type"] = "text/markdown; charset=utf-8"
    response.headers["Content-Disposition"] = "attachment; filename=repopilot-report.md"
    return response


@api.get("/report")
def report_page():
    context = _context(view="report")
    context["report_markdown_url"] = "/api/report/markdown"
    return render_template("report.html", **context)


def _refactor(file_path: str):
    item = _file(file_path)
    if item is None:
        return None
    before = item.content
    if item.language == "Python":
        after = before.replace("\n\n", "\n", 1) if "\n\n" in before else before.rstrip() + "\n"
        change = "Consolidated the first blank-line gap while preserving source semantics."
    else:
        after = before.rstrip() + "\n"
        change = "Normalized trailing whitespace while preserving source semantics."
    projected = max(0, item.risk_score - (3 if after != before else 0))
    return {"view": "refactor", "deterministic": True, "file": item.path, "strategy": "safe_readability_pass", "before": before, "after": after, "changes": [change] if after != before else [], "risk_before": item.risk_score, "risk_after": projected, "summary": "Preview only; no files were written."}


@api.get("/api/refactor")
@api.get("/refactor")
def refactor_query():
    file_path = request.args.get("file") or request.args.get("path")
    result = _refactor(file_path or "")
    if not _wants_json():
        context = _context(view="refactor")
        context.update({"refactor": result, "selected_path": file_path or (context["files"][0]["path"] if context["files"] else "")})
        return render_template("refactor.html", **context), 200 if result else 404
    return jsonify(result or {"error": "file_not_found", "message": f"No analyzed file named '{file_path}'."}), 200 if result else 404


@api.get("/api/refactor/<path:file_path>")
@api.get("/refactor/<path:file_path>")
def refactor_path(file_path: str):
    result = _refactor(file_path)
    return jsonify(result or {"error": "file_not_found", "message": f"No analyzed file named '{file_path}'."}), 200 if result else 404
