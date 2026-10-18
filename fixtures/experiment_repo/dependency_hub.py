"""High-reach module with intentionally many branches and local imports."""

import complex_hotspot
import cycle_a
import healthy
import refactor_candidate


def dispatch(value: int, mode: str) -> int:
    if mode == "complex":
        result = complex_hotspot.score_value(value)
    elif mode == "refactor":
        result = refactor_candidate.transform(value)
    elif mode == "cycle":
        result = cycle_a.cycle_value(value)
    else:
        result = healthy.identity(value)
    if result < 0:
        result = -result
    if result == 0:
        result = healthy.HEALTHY_VALUE
    return result


def classify_batch(values: list[int], mode: str) -> list[int]:
    output = []
    for value in values:
        if value % 2 == 0:
            output.append(dispatch(value, mode))
        elif value > 10:
            output.append(dispatch(value - 1, mode))
        else:
            output.append(dispatch(value + 1, mode))
    if len(output) > 10:
        output = output[:10]
    return output


def guarded(value: int) -> str:
    if value < 0:
        return "negative"
    if value == 0:
        return "zero"
    if value < 5:
        return "small"
    if value < 50:
        return "medium"
    if value < 500:
        return "large"
    return "huge"
