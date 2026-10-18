"""Complex code with intentionally low Cobertura coverage."""


def choose(value: int) -> str:
    if value < 0:
        return "negative"
    if value == 0:
        return "zero"
    if value < 5:
        return "small"
    if value < 10:
        return "medium"
    if value < 20:
        return "large"
    return "huge"


def fold(values: list[int]) -> int:
    result = 0
    for value in values:
        if value % 2:
            result += value
        else:
            result -= value
        if result > 20:
            result //= 2
    return result


def describe(values: list[int]) -> dict[str, int]:
    counts = {"positive": 0, "negative": 0}
    for value in values:
        if value >= 0:
            counts["positive"] += 1
        else:
            counts["negative"] += 1
    return counts
