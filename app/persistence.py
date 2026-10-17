"""SQLite persistence for RepoPilot snapshots.

The persistence layer deliberately uses only Python's standard-library
``sqlite3`` module.  It stores the report model as normalized rows while
keeping the public dataclasses in :mod:`app.models` as the application-facing
representation.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from .models import FileAnalysis, RepositoryReport, RiskSignal


SCHEMA = """
CREATE TABLE IF NOT EXISTS repositories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    branch TEXT NOT NULL,
    commit_hash TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
    id TEXT PRIMARY KEY,
    sequence INTEGER NOT NULL UNIQUE,
    repository_id INTEGER NOT NULL REFERENCES repositories(id),
    generated_at TEXT NOT NULL,
    demo_mode INTEGER NOT NULL DEFAULT 0,
    weights_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    language TEXT NOT NULL,
    lines INTEGER NOT NULL,
    functions INTEGER NOT NULL,
    risk_score INTEGER NOT NULL,
    risk_level TEXT NOT NULL,
    summary TEXT NOT NULL,
    content TEXT NOT NULL,
    UNIQUE(snapshot_id, path)
);

CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    file_path TEXT NOT NULL,
    name TEXT NOT NULL,
    score INTEGER NOT NULL,
    level TEXT NOT NULL,
    explanation TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    UNIQUE(snapshot_id, file_path, name)
);

CREATE TABLE IF NOT EXISTS dependency_edges (
    snapshot_id TEXT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    source_path TEXT NOT NULL,
    target_path TEXT NOT NULL,
    PRIMARY KEY(snapshot_id, source_path, target_path)
);

CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def default_database_path(base_dir: str | os.PathLike[str] | None = None) -> Path:
    """Return the portable, instance-local default database path."""
    root = Path(base_dir) if base_dir is not None else Path(__file__).resolve().parent.parent / "instance"
    return root / "repopilot.sqlite3"


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def _decode_object(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


class SQLitePersistence:
    """Small repository for the snapshot model and its related rows."""

    def __init__(self, database_path: str | os.PathLike[str] | None = None) -> None:
        configured = database_path or os.environ.get("REPOPILOT_DATABASE_PATH") or os.environ.get("REPOPILOT_DATABASE")
        self.path = str(configured or default_database_path())
        if self.path != ":memory:":
            Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(SCHEMA)
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def has_snapshots(self) -> bool:
        row = self.connection.execute("SELECT 1 FROM snapshots LIMIT 1").fetchone()
        return row is not None

    def next_sequence(self) -> int:
        row = self.connection.execute("SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM snapshots").fetchone()
        return int(row["next_sequence"])

    def save_snapshot(
        self,
        snapshot_id: str,
        report: RepositoryReport,
        weights: dict[str, float],
        *,
        make_current: bool = True,
    ) -> None:
        repository = dict(report.repository)
        identity = str(repository.get("path") or repository.get("name") or "repository")
        with self.connection:
            self.connection.execute(
                """INSERT INTO repositories(identity, name, path, branch, commit_hash, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(identity) DO UPDATE SET
                     name=excluded.name, path=excluded.path, branch=excluded.branch,
                     commit_hash=excluded.commit_hash, metadata_json=excluded.metadata_json""",
                (
                    identity,
                    str(repository.get("name", "repository")),
                    str(repository.get("path", "")),
                    str(repository.get("branch", "unknown")),
                    str(repository.get("commit", "unknown")),
                    _json(repository),
                ),
            )
            repository_row = self.connection.execute("SELECT id FROM repositories WHERE identity = ?", (identity,)).fetchone()
            assert repository_row is not None
            sequence = self.next_sequence()
            self.connection.execute(
                "INSERT INTO snapshots(id, sequence, repository_id, generated_at, demo_mode, weights_json) VALUES (?, ?, ?, ?, ?, ?)",
                (snapshot_id, sequence, repository_row["id"], report.generated_at, int(report.demo_mode), _json(weights)),
            )
            for item in report.files:
                self.connection.execute(
                    """INSERT INTO files(snapshot_id, path, language, lines, functions, risk_score, risk_level, summary, content)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (snapshot_id, item.path, item.language, item.lines, item.functions, item.risk_score, item.risk_level, item.summary, item.content),
                )
                for signal in item.signals:
                    self.connection.execute(
                        """INSERT INTO findings(snapshot_id, file_path, name, score, level, explanation, evidence_json)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (snapshot_id, item.path, signal.name, signal.score, signal.level, signal.explanation, _json(signal.evidence)),
                    )
                self.connection.executemany(
                    "INSERT INTO dependency_edges(snapshot_id, source_path, target_path) VALUES (?, ?, ?)",
                    [(snapshot_id, item.path, dependency) for dependency in item.dependencies],
                )
            if make_current:
                self.set_current_snapshot(snapshot_id)

    def set_current_snapshot(self, snapshot_id: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO app_state(key, value) VALUES ('current_snapshot_id', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (snapshot_id,),
            )

    def current_snapshot_id(self) -> str | None:
        row = self.connection.execute("SELECT value FROM app_state WHERE key = 'current_snapshot_id'").fetchone()
        return str(row["value"]) if row else None

    def load_weights(self, default: dict[str, float]) -> dict[str, float]:
        row = self.connection.execute("SELECT value FROM app_state WHERE key = 'weights'").fetchone()
        if not row:
            return dict(default)
        loaded = _decode_object(row["value"], {})
        if not isinstance(loaded, dict):
            return dict(default)
        return {name: float(loaded.get(name, value)) for name, value in default.items()}

    def save_weights(self, weights: dict[str, float]) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO app_state(key, value) VALUES ('weights', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (_json(weights),),
            )

    def load_snapshots(self) -> list[tuple[str, RepositoryReport, dict[str, float]]]:
        snapshots = self.connection.execute(
            """SELECT snapshots.id, snapshots.generated_at, snapshots.demo_mode, snapshots.weights_json,
                      repositories.metadata_json, repositories.name, repositories.path,
                      repositories.branch, repositories.commit_hash
                 FROM snapshots JOIN repositories ON repositories.id = snapshots.repository_id
                ORDER BY snapshots.sequence"""
        ).fetchall()
        result = []
        for snapshot in snapshots:
            repository = _decode_object(snapshot["metadata_json"], {})
            if not isinstance(repository, dict):
                repository = {}
            repository.setdefault("name", snapshot["name"])
            repository.setdefault("path", snapshot["path"])
            repository.setdefault("branch", snapshot["branch"])
            repository.setdefault("commit", snapshot["commit_hash"])
            file_rows = self.connection.execute(
                "SELECT path, language, lines, functions, risk_score, risk_level, summary, content FROM files WHERE snapshot_id = ? ORDER BY id",
                (snapshot["id"],),
            ).fetchall()
            signal_rows = self.connection.execute(
                "SELECT file_path, name, score, level, explanation, evidence_json FROM findings WHERE snapshot_id = ? ORDER BY id",
                (snapshot["id"],),
            ).fetchall()
            signals: dict[str, list[RiskSignal]] = {}
            for row in signal_rows:
                evidence = _decode_object(row["evidence_json"], [])
                signals.setdefault(row["file_path"], []).append(
                    RiskSignal(row["name"], row["score"], row["level"], row["explanation"], evidence if isinstance(evidence, list) else [])
                )
            edges = self.connection.execute(
                "SELECT source_path, target_path FROM dependency_edges WHERE snapshot_id = ? ORDER BY source_path, target_path",
                (snapshot["id"],),
            ).fetchall()
            dependencies: dict[str, list[str]] = {}
            dependents: dict[str, list[str]] = {}
            for edge in edges:
                dependencies.setdefault(edge["source_path"], []).append(edge["target_path"])
                dependents.setdefault(edge["target_path"], []).append(edge["source_path"])
            files = [
                FileAnalysis(
                    path=row["path"], language=row["language"], lines=row["lines"], functions=row["functions"],
                    dependencies=dependencies.get(row["path"], []), dependents=dependents.get(row["path"], []),
                    risk_score=row["risk_score"], risk_level=row["risk_level"], signals=signals.get(row["path"], []),
                    summary=row["summary"], content=row["content"],
                )
                for row in file_rows
            ]
            weights = _decode_object(snapshot["weights_json"], {})
            result.append((snapshot["id"], RepositoryReport(repository, files, snapshot["generated_at"], bool(snapshot["demo_mode"])), weights if isinstance(weights, dict) else {}))
        return result
