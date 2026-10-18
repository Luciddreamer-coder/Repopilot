"""Persistent snapshot history for the RepoPilot backend."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .demo_data import demo_report
from .models import FileAnalysis, RepositoryReport
from .persistence import SQLitePersistence
from .scanner import _level, scan_repository


DEFAULT_WEIGHTS = {"complexity": 0.45, "change_frequency": 0.25, "dependency_reach": 0.30}


def _weighted_report(report: RepositoryReport, weights: dict[str, float]) -> RepositoryReport:
    """Return a copy whose risk scores use the configured signal weights."""
    files: list[FileAnalysis] = []
    for item in report.files:
        scores = {signal.name: signal.score for signal in item.signals}
        risk = round(sum(scores.get(name, 0) * weights.get(name, DEFAULT_WEIGHTS[name]) for name in DEFAULT_WEIGHTS))
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
        body["summary"]["dependency_count"] = sum(len(item.dependencies) for item in self.report.files)
        return {
            "id": self.id,
            "generated_at": self.report.generated_at,
            "repository": self.report.repository,
            "summary": body["summary"],
            "weights": dict(self.weights),
            "demo_mode": self.report.demo_mode,
        }


class SnapshotStore:
    """SQLite-backed history, seeded with the demo scan only for a new DB."""

    def __init__(self, database_path: str | None = None) -> None:
        self.persistence = SQLitePersistence(database_path)
        self.weights = self.persistence.load_weights(DEFAULT_WEIGHTS)
        self._snapshots: list[Snapshot] = []
        loaded = self.persistence.load_snapshots()
        if not loaded:
            demo = demo_report()
            baseline = deepcopy(demo)
            baseline.generated_at = "2026-10-17T21:33:38+00:00"
            baseline_item = next((item for item in baseline.files if item.path == "src/api/routes.py"), None)
            if baseline_item is not None:
                baseline_item.lines = 209
            baseline_item.risk_score = 86
            baseline_item.risk_level = _level(baseline_item.risk_score)
            baseline_item.summary = "Earlier high-impact API boundary before the latest cleanup."
            for signal in baseline_item.signals:
                if signal.name == "complexity":
                    signal.score = 92
                    signal.level = _level(signal.score)
                    signal.explanation = "The earlier snapshot had deeper branching and larger handlers."
            self._add_snapshot(baseline, make_current=False)
            self._current = demo
            self._add_snapshot(demo)
        else:
            for snapshot_id, report, snapshot_weights in loaded:
                self._snapshots.append(Snapshot(snapshot_id, report, snapshot_weights or dict(DEFAULT_WEIGHTS)))
            current_id = self.persistence.current_snapshot_id()
            current = self.snapshot(current_id) if current_id else None
            self._current = current.report if current else self._snapshots[-1].report
            if current is None:
                self.persistence.set_current_snapshot(self._snapshots[-1].id)
        self._current_id = self.persistence.current_snapshot_id() or (self._snapshots[-1].id if self._snapshots else None)

    @property
    def report(self) -> RepositoryReport:
        return _weighted_report(self._current, self.weights)

    @property
    def snapshots(self) -> list[Snapshot]:
        return list(self._snapshots)

    @property
    def current_snapshot_id(self) -> str | None:
        return self._current_id

    def _add_snapshot(self, report: RepositoryReport, *, make_current: bool = True) -> Snapshot:
        snapshot = Snapshot(f"snapshot-{self.persistence.next_sequence():03d}", deepcopy(report), dict(self.weights))
        self.persistence.save_snapshot(snapshot.id, snapshot.report, snapshot.weights, make_current=make_current)
        self._snapshots.append(snapshot)
        if make_current:
            self._current_id = snapshot.id
        return snapshot

    def scan(self, repository_path: str | None) -> RepositoryReport:
        self._current = scan_repository(repository_path)
        self._add_snapshot(self._current)
        return self.report

    def snapshot(self, snapshot_id: str | None) -> Snapshot | None:
        if snapshot_id is None:
            return next((item for item in self._snapshots if item.id == self._current_id), self._snapshots[-1] if self._snapshots else None)
        return next((item for item in self._snapshots if item.id == snapshot_id), None)

    def select_snapshot(self, snapshot_id: str) -> Snapshot | None:
        snapshot = self.snapshot(snapshot_id)
        if snapshot is None:
            return None
        self._current = snapshot.report
        self._current_id = snapshot.id
        self.persistence.set_current_snapshot(snapshot.id)
        return snapshot

    def previous_snapshot(self, snapshot_id: str | None = None) -> Snapshot | None:
        """Return the snapshot immediately before the selected/current one."""
        current = self.snapshot(snapshot_id)
        if current is None:
            return None
        index = next((index for index, item in enumerate(self._snapshots) if item.id == current.id), -1)
        return self._snapshots[index - 1] if index > 0 else None

    def set_weights(self, weights: dict[str, float]) -> dict[str, float]:
        self.weights = dict(weights)
        self.persistence.save_weights(self.weights)
        return dict(self.weights)

    def close(self) -> None:
        self.persistence.close()
