"""HTTP routes for the RepoPilot prototype and its backend view models."""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from typing import Any

from flask import Blueprint, Response, current_app, jsonify, render_template, request

from .history import DEFAULT_WEIGHTS, SnapshotStore

api = Blueprint("api", __name__)


def _store() -> SnapshotStore:
    return current_app.extensions["repopilot_store"]


def _report():
    return _store().report


def _payload(report=None, view: str = "dashboard"):
    report = report or _report()
    body = report.to_dict()
    body["view"] = view
    return body


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
    body.update({"history": _history_body(), "findings": _findings(_report())[:8], "weights": dict(_store().weights), "graph": _graph(_report()), "file_options": [item.to_dict() for item in _report().files]})
    return body


def _requested_path() -> str | None:
    data = request.get_json(silent=True) or request.form
    return data.get("repo_path") or request.args.get("repo_path") or request.args.get("path")


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
    body = {"view": "file_detail", "repository": _report().repository, "file": item.to_dict(include_content=True)}
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
            }
            for item in report.files
        ),
        key=lambda item: (-item["change_count"], -item["lines_added"] - item["lines_deleted"], item["path"]),
    )
    return jsonify({"view": "git_activity", "repository": report.repository, "window_days": report.repository.get("git", {}).get("window_days", 90), "files": files})


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
    base_id = request.args.get("base_snapshot") or request.args.get("base") or request.args.get("before")
    target_id = request.args.get("target_snapshot") or request.args.get("target") or request.args.get("after")
    base = _store().snapshot(base_id) if base_id else (snapshots[-2] if len(snapshots) > 1 else snapshots[-1] if snapshots else None)
    target = _store().snapshot(target_id) if target_id else (snapshots[-1] if snapshots else None)
    if base is None or target is None:
        return jsonify({"error": "no_snapshots", "message": "No repository snapshots are available."}), 404
    before = _repository_summary(base.report)
    after = _repository_summary(target.report)
    before_paths = {item.path: item for item in base.report.files}
    after_paths = {item.path: item for item in target.report.files}
    changed = []
    for path in sorted(set(before_paths) | set(after_paths)):
        left, right = before_paths.get(path), after_paths.get(path)
        if left is None or right is None or left.risk_score != right.risk_score or left.lines != right.lines or left.dependencies != right.dependencies:
            changed.append({"path": path, "status": "added" if left is None else "removed" if right is None else "changed", "before": left.to_dict() if left else None, "after": right.to_dict() if right else None})
    before_view = {"snapshot_id": base.id, "repository": base.report.repository, "summary": before}
    after_view = {"snapshot_id": target.id, "repository": target.report.repository, "summary": after}
    finding_changes = _finding_lifecycle(target.report, base.report, target.id, base.id)
    body = {"view": "repository_compare", "base": before_view, "target": after_view, "before": before_view, "after": after_view, "delta": {key: after[key] - before[key] for key in ("file_count", "average_risk", "dependency_count")}, "changed_files": changed, "finding_changes": _finding_change_summary(finding_changes)}
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
    return {"file": item.to_dict(), "direct_dependencies": list(item.dependencies), "direct_dependents": list(item.dependents), "affected_files": [entry.path for entry in affected], "affected_count": len(affected), "risk_score": item.risk_score, "estimated_change_surface": "high" if len(affected) >= 3 or item.risk_score >= 75 else "medium" if affected else "low"}


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
    nodes = [{
        "id": item.path,
        "label": item.path,
        "language": item.language,
        "risk_score": item.risk_score,
        "risk_level": item.risk_level,
        "unresolved_imports": list(item.unresolved_imports),
        "cycles": [list(cycle) for cycle in item.dependency_cycles],
    } for item in report.files]
    edges = [{"source": item.path, "target": dependency} for item in report.files for dependency in item.dependencies]
    unresolved = [
        {"file": path, "imports": list(imports)}
        for path, imports in sorted(report.unresolved_imports.items()) if imports
    ]
    return {
        "view": "dependency_graph",
        "repository": report.repository,
        "nodes": nodes,
        "edges": edges,
        "unresolved_imports": unresolved,
        "cycles": [list(cycle) for cycle in report.dependency_cycles],
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
        lines.append(f"- **{finding['severity'].title()} — {finding['file']} ({finding['signal']}, {finding['score']})**: {finding['explanation']}")
    lines.extend(["", "## Files", "", "| File | Risk | Level | Lines | Dependencies |", "| --- | ---: | --- | ---: | ---: |"])
    for item in report.files:
        lines.append(f"| `{item.path}` | {item.risk_score} | {item.risk_level} | {item.lines} | {len(item.dependencies)} |")
    return "\n".join(lines) + "\n"


@api.get("/api/report/markdown")
@api.get("/report/markdown")
@api.get("/report.md")
def markdown_report():
    response = Response(_markdown_report(_report()), mimetype="text/markdown")
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
