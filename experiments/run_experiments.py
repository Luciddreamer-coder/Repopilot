"""Run the controlled RepoPilot fixture experiments.

The runner intentionally uses only the Python standard library and the local
``app.scanner`` module.  Every experiment scans either the checked-in fixture
or a temporary copy, so running it never mutates the fixture repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.scanner import scan_repository  # noqa: E402


DEFAULT_FIXTURE = PROJECT_ROOT / "fixtures" / "experiment_repo"
DEFAULT_OUTPUT = PROJECT_ROOT / "reports" / "experiments"
ANNOTATIONS_FILE = "annotations.json"
TARGET_FILE = "refactor_candidate.py"


def _load_annotations(fixture_root: Path) -> dict[str, Any]:
    with (fixture_root / ANNOTATIONS_FILE).open(encoding="utf-8") as stream:
        return json.load(stream)


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _edges(report: Any) -> list[dict[str, str]]:
    return [
        {"source": item.path, "target": dependency}
        for item in report.files
        for dependency in item.dependencies
    ]


def _sorted_edges(edges: list[dict[str, str]]) -> list[dict[str, str]]:
    return sorted(edges, key=lambda edge: (edge["source"], edge["target"]))


def experiment_a(fixture_root: Path, annotations: dict[str, Any]) -> dict[str, Any]:
    """Measure precision@5 for the scanner's risk ranking."""
    report = scan_repository(fixture_root)
    top_5 = [item.path for item in report.hotspots[:5]]
    expected = list(annotations["hotspots"])
    hits = [path for path in top_5 if path in expected]
    false_positives = [path for path in top_5 if path not in expected]
    missed_hotspots = [path for path in expected if path not in top_5]
    return {
        "name": "hotspot precision@5",
        "top_5": top_5,
        "annotated_hotspots": expected,
        "hits": hits,
        "false_positives": false_positives,
        "missed_hotspots": missed_hotspots,
        "precision_at_5": round(len(hits) / 5, 3),
    }


def experiment_b(fixture_root: Path) -> dict[str, Any]:
    """Compare a baseline scan with a scan after a temporary refactor."""
    before_report = scan_repository(fixture_root)
    before = next(item for item in before_report.files if item.path == TARGET_FILE)
    fixture_digest_before = _file_digest(fixture_root / TARGET_FILE)
    refactored_source = '''"""Simplified after-state implementation."""\n\n\ndef transform(value: int) -> int:\n    return abs(value) + 1\n'''

    with tempfile.TemporaryDirectory(prefix="repopilot-experiment-") as directory:
        copied_root = Path(directory) / fixture_root.name
        shutil.copytree(fixture_root, copied_root)
        (copied_root / TARGET_FILE).write_text(refactored_source, encoding="utf-8")
        after_report = scan_repository(copied_root)
        after = next(item for item in after_report.files if item.path == TARGET_FILE)

    return {
        "name": "before/after refactor scan comparison",
        "target": TARGET_FILE,
        "temporary_copy_used": True,
        "fixture_unchanged": fixture_digest_before == _file_digest(fixture_root / TARGET_FILE),
        "before": {
            "risk_score": before.risk_score,
            "risk_level": before.risk_level,
            "lines": before.lines,
            "complexity_score": next(signal.score for signal in before.signals if signal.name == "complexity"),
            "dependencies": list(before.dependencies),
        },
        "after": {
            "risk_score": after.risk_score,
            "risk_level": after.risk_level,
            "lines": after.lines,
            "complexity_score": next(signal.score for signal in after.signals if signal.name == "complexity"),
            "dependencies": list(after.dependencies),
        },
        "risk_delta": after.risk_score - before.risk_score,
        "complexity_delta": next(signal.score for signal in after.signals if signal.name == "complexity") - next(signal.score for signal in before.signals if signal.name == "complexity"),
        "improved": after.risk_score < before.risk_score,
    }


def experiment_c(fixture_root: Path, annotations: dict[str, Any]) -> dict[str, Any]:
    """Compare annotated resolved, unresolved, and circular graph data."""
    report = scan_repository(fixture_root)
    expected_edges = _sorted_edges([
        {"source": source, "target": target}
        for source, targets in annotations["dependencies"].items()
        for target in targets
    ])
    detected_edges = _sorted_edges(_edges(report))
    expected_unresolved = {
        path: sorted(imports) for path, imports in annotations["unresolved_imports"].items()
    }
    detected_unresolved = {
        path: sorted(imports) for path, imports in report.unresolved_imports.items()
    }
    expected_cycles = sorted(sorted(cycle) for cycle in annotations["cycles"])
    detected_cycles = sorted(sorted(cycle) for cycle in report.dependency_cycles)
    return {
        "name": "dependency graph detection",
        "expected_edges": expected_edges,
        "detected_edges": detected_edges,
        "missing_edges": [edge for edge in expected_edges if edge not in detected_edges],
        "unexpected_edges": [edge for edge in detected_edges if edge not in expected_edges],
        "expected_unresolved_imports": expected_unresolved,
        "detected_unresolved_imports": detected_unresolved,
        "expected_cycles": expected_cycles,
        "detected_cycles": detected_cycles,
        "matches": expected_edges == detected_edges and expected_unresolved == detected_unresolved and expected_cycles == detected_cycles,
    }


WEIGHT_CONFIGURATIONS: tuple[tuple[str, dict[str, float]], ...] = (
    ("balanced", {"complexity": 0.45, "change_frequency": 0.25, "dependency_reach": 0.30}),
    ("complexity_heavy", {"complexity": 0.65, "change_frequency": 0.15, "dependency_reach": 0.20}),
    ("reach_heavy", {"complexity": 0.20, "change_frequency": 0.15, "dependency_reach": 0.65}),
    ("change_heavy", {"complexity": 0.20, "change_frequency": 0.60, "dependency_reach": 0.20}),
)


def _weighted_ranking(report: Any, weights: dict[str, float]) -> list[dict[str, Any]]:
    ranked = []
    for item in report.files:
        signals = {signal.name: signal.score for signal in item.signals}
        score = round(sum(signals.get(name, 0) * weight for name, weight in weights.items()), 2)
        ranked.append({"path": item.path, "score": score})
    ranked.sort(key=lambda item: (-item["score"], item["path"]))
    for index, item in enumerate(ranked, start=1):
        item["rank"] = index
    return ranked


def experiment_d(fixture_root: Path) -> dict[str, Any]:
    """Show ranking order and top-five overlap under several weights."""
    report = scan_repository(fixture_root)
    rankings: dict[str, Any] = {}
    baseline_top_5: set[str] | None = None
    for name, weights in WEIGHT_CONFIGURATIONS:
        ranking = _weighted_ranking(report, weights)
        top_5 = {item["path"] for item in ranking[:5]}
        if baseline_top_5 is None:
            baseline_top_5 = top_5
        rankings[name] = {
            "weights": weights,
            "top_5": [item["path"] for item in ranking[:5]],
            "top_5_overlap_with_balanced": round(len(top_5 & baseline_top_5) / 5, 3),
            "ranking": ranking,
        }
    return {
        "name": "ranking robustness across weight configurations",
        "configurations": rankings,
        "balanced_top_5_stable_count": len({
            path
            for configuration in rankings.values()
            for path in configuration["top_5"]
        }),
    }


def _markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines.extend("| " + " | ".join(str(value) for value in row) + " |" for row in rows)
    return "\n".join(lines)


def _render_markdown(results: dict[str, Any]) -> str:
    a = results["A"]
    b = results["B"]
    c = results["C"]
    d = results["D"]
    lines = [
        "# RepoPilot controlled experiment evidence",
        "",
        "Fixture: `fixtures/experiment_repo`",
        "",
        "## Experiment A — hotspot precision@5",
        "",
        _markdown_table(
            ["Metric", "Value"],
            [["Top 5", ", ".join(f"`{path}`" for path in a["top_5"])], ["Annotated hits", ", ".join(f"`{path}`" for path in a["hits"])], ["Review candidates", ", ".join(f"`{path}`" for path in a["false_positives"]) or "none"], ["Precision@5", a["precision_at_5"]]],
        ),
        "",
        "## Experiment B — before/after refactor scan comparison",
        "",
        _markdown_table(
            ["Metric", "Before", "After", "Delta"],
            [["Risk score", b["before"]["risk_score"], b["after"]["risk_score"], b["risk_delta"]], ["Complexity score", b["before"]["complexity_score"], b["after"]["complexity_score"], b["complexity_delta"]], ["Lines", b["before"]["lines"], b["after"]["lines"], b["after"]["lines"] - b["before"]["lines"]]],
        ),
        "",
        f"Improved: **{b['improved']}**. Temporary copy used: **{b['temporary_copy_used']}**. Fixture unchanged: **{b['fixture_unchanged']}**.",
        "",
        "## Experiment C — dependency graph detection",
        "",
        f"All expected graph results match: **{c['matches']}**.",
        "",
        _markdown_table(
            ["Result type", "Expected", "Detected"],
            [["Resolved edges", len(c["expected_edges"]), len(c["detected_edges"])], ["Unresolved import files", len(c["expected_unresolved_imports"]), len(c["detected_unresolved_imports"])], ["Circular components", len(c["expected_cycles"]), len(c["detected_cycles"])]],
        ),
        "",
        "## Experiment D — ranking robustness across weight configurations",
        "",
        _markdown_table(
            ["Configuration", "Weights", "Top 5 overlap with balanced"],
            [[name, json.dumps(configuration["weights"], sort_keys=True), configuration["top_5_overlap_with_balanced"]] for name, configuration in d["configurations"].items()],
        ),
        "",
        "The full per-file ranking and machine-readable evidence are in `experiments.json`.",
        "",
    ]
    return "\n".join(lines)


def run_experiments(fixture_root: Path | str = DEFAULT_FIXTURE, output_dir: Path | str = DEFAULT_OUTPUT) -> dict[str, Any]:
    """Run all experiments and write JSON/Markdown evidence."""
    fixture = Path(fixture_root).resolve()
    output = Path(output_dir).resolve()
    annotations = _load_annotations(fixture)
    results = {
        "fixture": "fixtures/experiment_repo" if fixture == DEFAULT_FIXTURE.resolve() else fixture.name,
        "A": experiment_a(fixture, annotations),
        "B": experiment_b(fixture),
        "C": experiment_c(fixture, annotations),
        "D": experiment_d(fixture),
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "experiments.json").write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "experiments.md").write_text(_render_markdown(results), encoding="utf-8")
    return results


def _check_invariants(results: dict[str, Any]) -> list[str]:
    """Return regression messages for the experiment acceptance criteria."""
    failures: list[str] = []
    if results["A"]["precision_at_5"] < 0.8:
        failures.append(f"precision@5 {results['A']['precision_at_5']} is below 0.8")
    if not results["B"]["improved"] or not results["B"]["fixture_unchanged"]:
        failures.append("refactor comparison did not improve risk or changed the fixture")
    if not results["C"]["matches"]:
        failures.append("dependency graph results do not match annotations")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="fail when precision, graph, or refactor invariants regress")
    args = parser.parse_args(argv)
    results = run_experiments(args.fixture, args.output_dir)
    print(f"Experiment evidence written to {args.output_dir / 'experiments.json'} and {args.output_dir / 'experiments.md'}")
    print(f"A precision@5={results['A']['precision_at_5']}; B improved={results['B']['improved']}; C matches={results['C']['matches']}; D configurations={len(results['D']['configurations'])}")
    if args.check:
        failures = _check_invariants(results)
        if failures:
            for failure in failures:
                print(f"experiment check failed: {failure}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
