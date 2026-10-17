"""Small serializable models used by the scanner and HTTP layer."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RiskSignal:
    name: str
    score: int
    level: str
    explanation: str
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "score": self.score,
            "level": self.level,
            "explanation": self.explanation,
            "evidence": self.evidence,
        }


@dataclass
class FileAnalysis:
    path: str
    language: str
    lines: int
    functions: int
    dependencies: list[str]
    dependents: list[str]
    risk_score: int
    risk_level: str
    signals: list[RiskSignal]
    summary: str
    content: str = ""
    change_count: int = 0
    lines_added: int = 0
    lines_deleted: int = 0
    authors: int = 0
    last_changed: str | None = None
    coverage_percent: float | None = None
    coverage_covered_lines: int | None = None
    coverage_total_lines: int | None = None
    coverage_missing_lines: int | None = None
    coverage_source: str | None = None
    unresolved_imports: list[str] = field(default_factory=list)
    dependency_cycles: list[list[str]] = field(default_factory=list)

    def to_dict(self, include_content: bool = False) -> dict[str, Any]:
        result = {
            "path": self.path,
            "language": self.language,
            "lines": self.lines,
            "functions": self.functions,
            "dependencies": self.dependencies,
            "dependents": self.dependents,
            "risk_score": self.risk_score,
            "risk_level": self.risk_level,
            "signals": [signal.to_dict() for signal in self.signals],
            "summary": self.summary,
            "git": {
                "change_count": self.change_count,
                "lines_added": self.lines_added,
                "lines_deleted": self.lines_deleted,
                "authors": self.authors,
                "last_changed": self.last_changed,
            },
            "coverage": {
                "percent": self.coverage_percent,
                "covered_lines": self.coverage_covered_lines,
                "total_lines": self.coverage_total_lines,
                "missing_lines": self.coverage_missing_lines,
                "source": self.coverage_source,
            } if self.coverage_percent is not None else None,
            # Keep the scalar names convenient for API consumers that do not
            # want to unpack the nested coverage object.
            "coverage_percent": self.coverage_percent,
            "coverage_covered_lines": self.coverage_covered_lines,
            "coverage_total_lines": self.coverage_total_lines,
            "coverage_missing_lines": self.coverage_missing_lines,
            "coverage_source": self.coverage_source,
            "unresolved_imports": list(self.unresolved_imports),
            "dependency_cycles": [list(cycle) for cycle in self.dependency_cycles],
        }
        if include_content:
            result["content"] = self.content
        return result


@dataclass
class RepositoryReport:
    repository: dict[str, Any]
    files: list[FileAnalysis]
    generated_at: str
    demo_mode: bool
    unresolved_imports: dict[str, list[str]] = field(default_factory=dict)
    dependency_cycles: list[list[str]] = field(default_factory=list)

    @property
    def average_risk(self) -> int:
        return round(sum(item.risk_score for item in self.files) / len(self.files)) if self.files else 0

    @property
    def hotspots(self) -> list[FileAnalysis]:
        return sorted(self.files, key=lambda item: item.risk_score, reverse=True)

    def to_dict(self, include_content: bool = False) -> dict[str, Any]:
        counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for item in self.files:
            counts[item.risk_level] = counts.get(item.risk_level, 0) + 1
        return {
            "repository": self.repository,
            "generated_at": self.generated_at,
            "demo_mode": self.demo_mode,
            "summary": {
                "file_count": len(self.files),
                "average_risk": self.average_risk,
                "risk_counts": counts,
                "hotspots": [item.to_dict() for item in self.hotspots[:5]],
            },
            "files": [item.to_dict(include_content=include_content) for item in self.files],
            "dependency_graph": {
                "unresolved_imports": {
                    path: list(imports) for path, imports in sorted(self.unresolved_imports.items()) if imports
                },
                "cycles": [list(cycle) for cycle in self.dependency_cycles],
            },
        }
