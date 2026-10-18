"""Dependency-free command-line tooling for RepoPilot.

The CLI deliberately uses the same scanner and :class:`SnapshotStore` as the
web application.  It does not import Flask, so reporting and quality gates can
run in a minimal Python environment.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from typing import Any, Sequence
from pathlib import Path

from .history import SnapshotStore
from .models import RepositoryReport


def _findings(report: RepositoryReport) -> list[dict[str, Any]]:
    """Return the structured findings represented by the report's signals."""
    findings: list[dict[str, Any]] = []
    for item in report.files:
        for signal in item.signals:
            if signal.score < 40:
                continue
            findings.append(
                {
                    "id": f"{item.path}:{signal.name}",
                    "file": item.path,
                    "signal": signal.name,
                    "score": signal.score,
                    "severity": signal.level,
                    "title": f"{signal.name.replace('_', ' ').title()} risk in {item.path}",
                    "explanation": signal.explanation,
                    "evidence": list(signal.evidence),
                    "recommendation": f"Review {signal.name.replace('_', ' ')} before changing this file.",
                }
            )
        if item.unresolved_imports:
            imports = list(item.unresolved_imports)
            score = min(100, 65 + (len(imports) * 10))
            findings.append(
                {
                    "id": f"{item.path}:unresolved_imports",
                    "file": item.path,
                    "signal": "unresolved_imports",
                    "score": score,
                    "severity": "critical" if score >= 90 else "high" if score >= 75 else "medium",
                    "title": f"Unresolved imports in {item.path}",
                    "explanation": "The scanner found imports that do not resolve to files in the repository.",
                    "evidence": [f"Unresolved import: {name}" for name in imports],
                    "recommendation": "Resolve these imports or document the external dependency before changing this file.",
                }
            )
        if item.dependency_cycles:
            cycles = [list(cycle) for cycle in item.dependency_cycles]
            score = min(100, 80 + (len(cycles) * 10))
            findings.append(
                {
                    "id": f"{item.path}:dependency_cycles",
                    "file": item.path,
                    "signal": "dependency_cycles",
                    "score": score,
                    "severity": "critical" if score >= 90 else "high",
                    "title": f"Circular dependency involving {item.path}",
                    "explanation": "The file participates in one or more circular dependency components.",
                    "evidence": ["Circular dependency: " + " -> ".join(cycle + [cycle[0]]) for cycle in cycles],
                    "recommendation": "Break the cycle by extracting a lower-level module or inverting the dependency.",
                }
            )
        signals = {signal.name: signal for signal in item.signals}
        high_risk = item.risk_level in {"high", "critical"} or item.risk_score >= 75
        low_coverage = signals.get("low_coverage")
        if high_risk and low_coverage is not None:
            score = min(100, max(item.risk_score, low_coverage.score) + 10)
            findings.append(
                {
                    "id": f"{item.path}:high_risk_low_coverage",
                    "file": item.path,
                    "signal": "high_risk_low_coverage",
                    "score": score,
                    "severity": "critical" if score >= 90 else "high",
                    "title": f"High risk with low coverage in {item.path}",
                    "explanation": "This high-risk module has measured line coverage below the analysis threshold.",
                    "evidence": [f"risk score: {item.risk_score}", *low_coverage.evidence],
                    "recommendation": "Add focused tests for uncovered paths before changing this high-risk module.",
                }
            )
        change_signal = signals.get("change_frequency")
        if high_risk and item.change_count >= 2 and change_signal is not None:
            score = min(100, max(item.risk_score, change_signal.score) + 10)
            findings.append(
                {
                    "id": f"{item.path}:high_risk_high_churn",
                    "file": item.path,
                    "signal": "high_risk_high_churn",
                    "score": score,
                    "severity": "critical" if score >= 90 else "high",
                    "title": f"High risk with high churn in {item.path}",
                    "explanation": "This high-risk module has changed repeatedly in the recent history window.",
                    "evidence": [f"risk score: {item.risk_score}", *change_signal.evidence],
                    "recommendation": "Review recent changes and add regression coverage before further edits.",
                }
            )
    return sorted(findings, key=lambda entry: (-entry["score"], entry["file"], entry["signal"]))


def _finding_lifecycle(
    current_report: RepositoryReport,
    previous_report: RepositoryReport | None,
    current_snapshot_id: str | None,
    previous_snapshot_id: str | None,
) -> list[dict[str, Any]]:
    """Classify current findings against the immediately previous snapshot."""
    current = {item["id"]: item for item in _findings(current_report)}
    previous = {item["id"]: item for item in _findings(previous_report)} if previous_report else {}
    lifecycle: list[dict[str, Any]] = []
    for finding_id, item in current.items():
        before = previous.get(finding_id)
        delta = item["score"] - before["score"] if before else item["score"]
        status = "new" if before is None else "regressed" if delta >= 3 else "improved" if delta <= -3 else "open"
        lifecycle.append(
            {
                **item,
                "status": status,
                "score_delta": delta,
                "previous_score": before["score"] if before else None,
                "first_seen_snapshot": current_snapshot_id if before is None else previous_snapshot_id,
                "resolved_in_snapshot": None,
            }
        )
    for finding_id, before in previous.items():
        if finding_id not in current:
            lifecycle.append(
                {
                    **before,
                    "status": "resolved",
                    "score_delta": -before["score"],
                    "previous_score": before["score"],
                    "first_seen_snapshot": previous_snapshot_id,
                    "resolved_in_snapshot": current_snapshot_id,
                }
            )
    return sorted(
        lifecycle,
        key=lambda entry: (
            0 if entry["status"] in {"new", "regressed"} else 1,
            -entry["score"],
            entry["file"],
            entry["signal"],
        ),
    )


def _markdown_report(report: RepositoryReport, findings: list[dict[str, Any]]) -> str:
    summary = report.to_dict()["summary"]
    lines = [
        f"# RepoPilot report: {report.repository.get('name', 'repository')}",
        "",
        f"- Generated: {report.generated_at}",
        f"- Branch: {report.repository.get('branch', 'unknown')}",
        f"- Files: {summary['file_count']}",
        f"- Average risk: {summary['average_risk']}",
        "",
        "## Findings",
        "",
    ]
    for finding in findings:
        lines.append(
            f"- **{finding['severity'].title()} - {finding['file']} "
            f"({finding['signal']}, {finding['score']})**: {finding['explanation']}"
        )
    lines.extend(
        [
            "",
            "## Files",
            "",
            "| File | Risk | Level | Lines | Dependencies |",
            "| --- | ---: | --- | ---: | ---: |",
        ]
    )
    for item in report.files:
        lines.append(f"| `{item.path}` | {item.risk_score} | {item.risk_level} | {item.lines} | {len(item.dependencies)} |")
    # Keep the report a real Unicode string until the caller writes it.  An
    # explicit UTF-8 round trip catches accidental surrogate values without
    # silently introducing replacement characters.
    rendered = "\n".join(lines) + "\n"
    return rendered.encode("utf-8", errors="strict").decode("utf-8")


def _json_output(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _write_markdown(text: str) -> None:
    """Write Markdown as UTF-8 bytes when stdout exposes a binary stream."""
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(text.encode("utf-8", errors="strict"))
        buffer.flush()
    else:  # StringIO and other test/caller-provided text streams.
        sys.stdout.write(text)


def _scan_store(path: str, database: str | None) -> tuple[SnapshotStore, RepositoryReport, list[dict[str, Any]]]:
    """Scan with an explicit DB, or an isolated throwaway DB for the CLI.

    The web app owns the instance database.  A CLI invocation without
    ``--database`` must never seed or append to that database, even when the
    ``REPOPILOT_DATABASE_PATH`` environment variable is configured for the
    server.
    """
    temporary_directory: tempfile.TemporaryDirectory[str] | None = None
    if database is None:
        temporary_directory = tempfile.TemporaryDirectory(prefix="repopilot-cli-")
        database = str(Path(temporary_directory.name) / "snapshots.sqlite3")
    try:
        store = SnapshotStore(database)
    except Exception:
        if temporary_directory is not None:
            temporary_directory.cleanup()
        raise
    if temporary_directory is not None:
        store._cli_temporary_directory = temporary_directory  # type: ignore[attr-defined]
    report = store.scan(path)
    # ``SnapshotStore`` may return a weighted copy that predates the
    # repository-level structural fields.  The durable per-file fields are
    # authoritative, so restore the aggregate maps before serializing output.
    report.unresolved_imports = {
        item.path: list(item.unresolved_imports) for item in report.files if item.unresolved_imports
    }
    report.dependency_cycles = [
        list(cycle)
        for cycle in sorted({tuple(cycle) for item in report.files for cycle in item.dependency_cycles})
    ]
    current = store.snapshot(None)
    previous = store.previous_snapshot(current.id if current else None)
    findings = _finding_lifecycle(
        report,
        previous.report if previous else None,
        current.id if current else None,
        previous.id if previous else None,
    )
    return store, report, findings


def _close_store(store: SnapshotStore) -> None:
    store.close()
    temporary_directory = getattr(store, "_cli_temporary_directory", None)
    if temporary_directory is not None:
        temporary_directory.cleanup()


def _report_payload(report: RepositoryReport, *, command: str, snapshot_id: str | None = None) -> dict[str, Any]:
    payload = report.to_dict()
    payload["command"] = command
    if snapshot_id is not None:
        payload["snapshot_id"] = snapshot_id
    return payload


def _command_scan(args: argparse.Namespace) -> int:
    store, report, _ = _scan_store(args.path, args.database)
    try:
        _json_output(_report_payload(report, command="scan", snapshot_id=store.current_snapshot_id))
    finally:
        _close_store(store)
    return 0


def _command_hotspots(args: argparse.Namespace) -> int:
    store, report, _ = _scan_store(args.path, args.database)
    try:
        _json_output(
            {
                "command": "hotspots",
                "repository": report.repository,
                "count": len(report.hotspots),
                "hotspots": [item.to_dict() for item in report.hotspots],
            }
        )
    finally:
        _close_store(store)
    return 0


def _command_findings(args: argparse.Namespace) -> int:
    store, report, findings = _scan_store(args.path, args.database)
    try:
        _json_output({"command": "findings", "repository": report.repository, "count": len(findings), "findings": findings})
    finally:
        _close_store(store)
    return 0


def _command_report(args: argparse.Namespace) -> int:
    store, report, _ = _scan_store(args.path, args.database)
    try:
        if args.format == "json":
            _json_output(_report_payload(report, command="report", snapshot_id=store.current_snapshot_id))
        else:
            _write_markdown(_markdown_report(report, _findings(report)))
    finally:
        _close_store(store)
    return 0


def _command_check(args: argparse.Namespace) -> int:
    store, report, findings = _scan_store(args.path, args.database)
    try:
        max_risk = max((item.risk_score for item in report.files), default=0)
        new_findings = sum(item["status"] == "new" for item in findings)
        violations = []
        if max_risk > args.max_risk:
            violations.append(f"maximum risk {max_risk} exceeds {args.max_risk}")
        if new_findings > args.max_new_findings:
            violations.append(f"new findings {new_findings} exceeds {args.max_new_findings}")
        payload = {
            "command": "check",
            "repository": report.repository,
            "passed": not violations,
            "max_risk": max_risk,
            "max_risk_allowed": args.max_risk,
            "new_findings": new_findings,
            "max_new_findings_allowed": args.max_new_findings,
            "violations": violations,
            "snapshot_id": store.current_snapshot_id,
        }
        _json_output(payload)
        return 0 if not violations else 1
    finally:
        _close_store(store)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="repopilot", description="Scan repositories and enforce RepoPilot risk gates.")
    parser.add_argument(
        "--database",
        metavar="PATH",
        help="persist CLI snapshots in PATH (default: temporary isolated database per invocation)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    scan = commands.add_parser("scan", help="scan PATH and print the complete report as JSON")
    scan.add_argument("path")
    scan.set_defaults(handler=_command_scan)

    hotspots = commands.add_parser("hotspots", help="scan PATH and print files ordered by risk")
    hotspots.add_argument("path")
    hotspots.set_defaults(handler=_command_hotspots)

    findings = commands.add_parser("findings", help="scan PATH and print structured findings")
    findings.add_argument("path")
    findings.set_defaults(handler=_command_findings)

    report = commands.add_parser("report", help="scan PATH and print a report")
    report.add_argument("path")
    report.add_argument("--format", choices=("markdown", "json"), required=True)
    report.set_defaults(handler=_command_report)

    check = commands.add_parser("check", help="scan PATH and enforce risk thresholds")
    check.add_argument("path")
    check.add_argument("--max-risk", type=_non_negative_int, required=True, metavar="N")
    check.add_argument("--max-new-findings", type=_non_negative_int, required=True, metavar="N")
    check.set_defaults(handler=_command_check)
    return parser


def _non_negative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer") from error
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (OSError, RuntimeError) as error:
        print(f"repopilot: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover - exercised through the wrapper
    raise SystemExit(main())
