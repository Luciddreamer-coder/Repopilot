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
        }
