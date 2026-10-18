"""Deliberately complex module used by RepoPilot experiments."""

from healthy import HEALTHY_VALUE


def score_value(value: int, limit: int = 10) -> int:
    total = 0
    for index in range(limit):
        if value > index:
            total += value * (index + 1)
        else:
            total -= index
        if total > 100:
            total //= 2
        elif total < -100:
            total = abs(total)
    return total + HEALTHY_VALUE


def classify(value: int) -> str:
    if value < 0:
        return "negative"
    if value == 0:
        return "zero"
    if value < 10:
        return "small"
    if value < 100:
        return "medium"
    return "large"


def normalize(values: list[int]) -> list[int]:
    normalized = []
    for value in values:
        if value < 0:
            normalized.append(0)
        elif value > 100:
            normalized.append(100)
        else:
            normalized.append(value)
    return normalized


def route(value: int, enabled: bool, retries: int) -> str:
    if not enabled:
        return "disabled"
    for attempt in range(retries):
        if attempt == 0 and value > 50:
            return "fast"
        if attempt > 1 and value < 5:
            return "slow"
        if value % 2 == 0:
            continue
        if value % 3 == 0:
            return "alternate"
    return "default"


def summarize(values: list[int]) -> dict[str, int]:
    result = {"positive": 0, "negative": 0, "zero": 0}
    for value in values:
        if value > 0:
            result["positive"] += 1
        elif value < 0:
            result["negative"] += 1
        else:
            result["zero"] += 1
    if result["positive"] > result["negative"]:
        result["dominant"] = result["positive"]
    else:
        result["dominant"] = result["negative"]
    return result
