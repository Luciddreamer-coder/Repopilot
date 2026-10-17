"""Small process-local snapshot store for the RepoPilot backend."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .demo_data import demo_report
from .models import FileAnalysis, RepositoryReport
from .scanner import _level, scan_repository


DEFAULT_WEIGHTS = {"complexity": 0.45, "change_frequency": 0.25, "dependency_reach": 0.30}


def _weighted_report(report: RepositoryReport, weights: dict[str, float]) -> RepositoryReport:
    """Return a copy whose risk scores use the configured signal weights."""
    files: list[FileAnalysis] = []
    for item in report.files:
        scores = {signal.name: signal.score for signal in item.signals}
        risk = round(sum(scores.get(name, 0) * weights[name] for name in DEFAULT_WEIGHTS))
        clone = deepcopy(item)
        clone.risk_score = risk
        clone.risk_level = _level(risk)
        files.append(clone)
    return RepositoryReport(deepcopy(report.repository), files, report.generated_at, report.demo_mode)


@dataclass
class Snapshot:
    id: str
    report: RepositoryReport
    weights: dict[str, float]

    def metadata(self) -> dict[str, Any]:
        body = self.report.to_dict()
        return {
            "id": self.id,
            "generated_at": self.report.generated_at,
            "repository": self.report.repository,
            "summary": body["summary"],
            "weights": dict(self.weights),
            "demo_mode": self.report.demo_mode,
        }


class SnapshotStore:
    """An intentionally small in-memory history, seeded with the demo scan."""

    def __init__(self) -> None:
        self.weights = dict(DEFAULT_WEIGHTS)
        self._snapshots: list[Snapshot] = []
        self._current = demo_report()
        self._add_snapshot(self._current)

    @property
    def report(self) -> RepositoryReport:
        return _weighted_report(self._current, self.weights)

    @property
    def snapshots(self) -> list[Snapshot]:
        return list(self._snapshots)

    def _add_snapshot(self, report: RepositoryReport) -> Snapshot:
        snapshot = Snapshot(f"snapshot-{len(self._snapshots) + 1:03d}", deepcopy(report), dict(self.weights))
        self._snapshots.append(snapshot)
        return snapshot

    def scan(self, repository_path: str | None) -> RepositoryReport:
        self._current = scan_repository(repository_path)
        self._add_snapshot(self._current)
        return self.report

    def snapshot(self, snapshot_id: str | None) -> Snapshot | None:
        if snapshot_id is None:
            return self._snapshots[-1] if self._snapshots else None
        return next((item for item in self._snapshots if item.id == snapshot_id), None)

    def set_weights(self, weights: dict[str, float]) -> dict[str, float]:
        self.weights = dict(weights)
        return dict(self.weights)
