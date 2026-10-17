"""Believable local demo repository used when no repository is supplied."""

from datetime import datetime, timezone

from .models import FileAnalysis, RepositoryReport, RiskSignal


def _signal(name: str, score: int, level: str, explanation: str, *evidence: str) -> RiskSignal:
    return RiskSignal(name, score, level, explanation, list(evidence))


def demo_report() -> RepositoryReport:
    files = [
        FileAnalysis(
            path="src/api/routes.py", language="Python", lines=184, functions=14,
            dependencies=["src/services/analyzer.py", "src/models/report.py"],
            dependents=["tests/test_routes.py", "src/app.py"], risk_score=78, risk_level="high",
            signals=[
                _signal("complexity", 82, "high", "Several request handlers combine validation, persistence, and response shaping.", "14 functions", "branch density: 0.18"),
                _signal("change_frequency", 76, "high", "This module has changed often in recent iterations.", "9 changes in the last 30 days"),
                _signal("dependency_reach", 75, "high", "A route change can affect the application entrypoint and route tests.", "2 direct dependencies", "2 downstream dependents"),
            ], summary="High-impact API boundary with multiple responsibilities.",
            content="from flask import Blueprint\n\napi = Blueprint('api', __name__)\n\n@api.get('/reports')\ndef reports():\n    return {'status': 'ok'}\n",
        ),
        FileAnalysis(
            path="src/services/analyzer.py", language="Python", lines=241, functions=18,
            dependencies=["src/models/report.py"], dependents=["src/api/routes.py"], risk_score=69, risk_level="high",
            signals=[
                _signal("complexity", 74, "high", "Analysis orchestration has nested conditionals and several scoring rules.", "18 functions", "branch density: 0.21"),
                _signal("change_frequency", 62, "medium", "The scoring model is an active area of development.", "6 changes in the last 30 days"),
                _signal("dependency_reach", 67, "high", "Results flow into the public API response.", "1 direct dependency", "1 downstream dependent"),
            ], summary="Core scoring engine; changes should be reviewed with API fixtures.",
            content="def score_file(metrics):\n    score = 0\n    if metrics.lines > 200:\n        score += 25\n    if metrics.change_frequency > 5:\n        score += 25\n    return score\n",
        ),
        FileAnalysis(
            path="src/models/report.py", language="Python", lines=96, functions=7,
            dependencies=[], dependents=["src/api/routes.py", "src/services/analyzer.py"], risk_score=43, risk_level="medium",
            signals=[
                _signal("complexity", 35, "low", "Data structures are mostly small and easy to follow.", "7 functions"),
                _signal("change_frequency", 44, "medium", "The model has received occasional schema updates.", "3 changes in the last 30 days"),
                _signal("dependency_reach", 51, "medium", "Two analysis paths depend on this model.", "2 downstream dependents"),
            ], summary="Shared report model with moderate downstream reach.",
            content="class Report:\n    def __init__(self, files):\n        self.files = files\n\n    @property\n    def average_risk(self):\n        return sum(item.risk for item in self.files) / len(self.files)\n",
        ),
        FileAnalysis(
            path="tests/test_routes.py", language="Python", lines=74, functions=6,
            dependencies=["src/api/routes.py"], dependents=[], risk_score=24, risk_level="low",
            signals=[
                _signal("complexity", 18, "low", "Test cases are short and have limited branching.", "6 functions"),
                _signal("change_frequency", 29, "low", "Tests change less frequently than production modules.", "1 change in the last 30 days"),
                _signal("dependency_reach", 25, "low", "No other files depend on this test module.", "0 downstream dependents"),
            ], summary="Low-risk route coverage with no downstream consumers.",
            content="def test_health(client):\n    response = client.get('/health')\n    assert response.status_code == 200\n",
        ),
        FileAnalysis(
            path="README.md", language="Markdown", lines=48, functions=0,
            dependencies=[], dependents=[], risk_score=12, risk_level="low",
            signals=[
                _signal("complexity", 5, "low", "Documentation has no executable branches.", "0 functions"),
                _signal("change_frequency", 18, "low", "Documentation is stable.", "1 change in the last 30 days"),
                _signal("dependency_reach", 10, "low", "No code imports documentation.", "0 downstream dependents"),
            ], summary="Stable documentation file.", content="# RepoPilot\n\nA sample repository.\n",
        ),
    ]
    return RepositoryReport(
        repository={"name": "repo-pilot-demo", "path": "demo://repo-pilot", "branch": "main", "commit": "demo-7f3a2c1"},
        files=files,
        generated_at=datetime.now(timezone.utc).isoformat(),
        demo_mode=True,
    )
