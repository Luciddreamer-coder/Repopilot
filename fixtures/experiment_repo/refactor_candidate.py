"""Before-state code for the before/after refactor experiment."""

import complex_hotspot


def transform(value: int) -> int:
    result = value
    if result < 0:
        result = -result
    if result > 10:
        result = complex_hotspot.score_value(result, 4)
    elif result == 10:
        result += 1
    else:
        result += 2
    for _ in range(3):
        if result % 2:
            result += 1
        else:
            result //= 2
    return result


def label(value: int) -> str:
    if value < 0:
        return "negative"
    if value < 10:
        return "small"
    if value < 100:
        return "medium"
    return "large"
