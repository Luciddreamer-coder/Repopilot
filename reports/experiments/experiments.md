# RepoPilot controlled experiment evidence

Fixture: `fixtures/experiment_repo`

## Experiment A — hotspot precision@5

| Metric | Value |
| --- | --- |
| Top 5 | `complex_hotspot.py`, `dependency_hub.py`, `refactor_candidate.py`, `low_coverage.py`, `cycle_a.py` |
| Annotated hits | `complex_hotspot.py`, `dependency_hub.py`, `refactor_candidate.py`, `low_coverage.py` |
| Review candidates | `cycle_a.py` |
| Precision@5 | 0.8 |

## Experiment B — before/after refactor scan comparison

| Metric | Before | After | Delta |
| --- | --- | --- | --- |
| Risk score | 32 | 10 | -22 |
| Complexity score | 43 | 3 | -40 |
| Lines | 31 | 5 | -26 |

Improved: **True**. Temporary copy used: **True**. Fixture unchanged: **True**.

## Experiment C — dependency graph detection

All expected graph results match: **True**.

| Result type | Expected | Detected |
| --- | --- | --- |
| Resolved edges | 12 | 12 |
| Unresolved import files | 1 | 1 |
| Circular components | 1 | 1 |

## Experiment D — ranking robustness across weight configurations

| Configuration | Weights | Top 5 overlap with balanced |
| --- | --- | --- |
| balanced | {"change_frequency": 0.25, "complexity": 0.45, "dependency_reach": 0.3} | 1.0 |
| complexity_heavy | {"change_frequency": 0.15, "complexity": 0.65, "dependency_reach": 0.2} | 1.0 |
| reach_heavy | {"change_frequency": 0.15, "complexity": 0.2, "dependency_reach": 0.65} | 0.8 |
| change_heavy | {"change_frequency": 0.6, "complexity": 0.2, "dependency_reach": 0.2} | 1.0 |

The full per-file ranking and machine-readable evidence are in `experiments.json`.
