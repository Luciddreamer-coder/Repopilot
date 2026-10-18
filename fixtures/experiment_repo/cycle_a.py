"""First half of the controlled circular import."""

import cycle_b


def cycle_value(value: int) -> int:
    if value:
        return cycle_b.bump(value)
    return 0
