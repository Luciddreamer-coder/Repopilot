"""Repeatable local scan benchmark for RepoPilot's performance milestone."""

from __future__ import annotations

import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.scanner import scan_repository


def make_fixture(root: Path, count: int = 80) -> None:
    for index in range(count):
        dependency = f"from module_{index - 1} import value\n" if index else ""
        (root / f"module_{index}.py").write_text(
            f"{dependency}\nvalue = {index}\n\ndef function_{index}(value):\n    if value > {index // 2}:\n        return value + {index}\n    return value\n",
            encoding="utf-8",
        )


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        make_fixture(root)
        cold_start = time.perf_counter()
        scan_repository(root)
        cold_ms = (time.perf_counter() - cold_start) * 1000
        warm_runs = []
        for _ in range(3):
            start = time.perf_counter()
            report = scan_repository(root)
            warm_runs.append((time.perf_counter() - start) * 1000)
        print(f"files={len(report.files)}")
        print(f"cold_ms={cold_ms:.2f}")
        print(f"warm_ms={statistics.median(warm_runs):.2f}")
        print(f"warm_samples_ms={[round(value, 2) for value in warm_runs]}")


if __name__ == "__main__":
    main()
