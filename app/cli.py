"""Dependency-free command-line tooling for RepoPilot.

The CLI deliberately uses the same scanner and :class:`SnapshotStore` as the
web application.  It does not import Flask, so reporting and quality gates can
run in a minimal Python environment.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Sequence

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
            f"- **{finding['severity'].title()} — {finding['file']} "
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
    return "\n".join(lines) + "\n"


def _json_output(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _scan_store(path: str, database: str | None) -> tuple[SnapshotStore, RepositoryReport, list[dict[str, Any]]]:
    store = SnapshotStore(database)
    report = store.scan(path)
    current = store.snapshot(None)
    previous = store.previous_snapshot(current.id if current else None)
    findings = _finding_lifecycle(
        report,
        previous.report if previous else None,
        current.id if current else None,
        previous.id if previous else None,
    )
    return store, report, findings


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
        store.close()
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
        store.close()
    return 0


def _command_findings(args: argparse.Namespace) -> int:
    store, report, findings = _scan_store(args.path, args.database)
    try:
        _json_output({"command": "findings", "repository": report.repository, "count": len(findings), "findings": findings})
    finally:
        store.close()
    return 0


def _command_report(args: argparse.Namespace) -> int:
    store, report, findings = _scan_store(args.path, args.database)
    try:
        if args.format == "json":
            _json_output(_report_payload(report, command="report", snapshot_id=store.current_snapshot_id))
        else:
            print(_markdown_report(report, findings), end="")
    finally:
        store.close()
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
        store.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="repopilot", description="Scan repositories and enforce RepoPilot risk gates.")
    parser.add_argument(
        "--database",
        help="SQLite snapshot database (defaults to REPOPILOT_DATABASE_PATH or the app instance database)",
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
