"""Second half of the controlled circular import."""

import cycle_a


def bump(value: int) -> int:
    if value > 1:
        return value + 1
    return cycle_a.cycle_value(value + 1)
