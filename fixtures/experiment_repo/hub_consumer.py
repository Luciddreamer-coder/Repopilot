"""Downstream consumer that makes dependency_hub a measurable hub."""

import complex_hotspot
import dependency_hub


def consume(value: int) -> int:
    if value > 10:
        return dependency_hub.dispatch(value, "complex")
    return complex_hotspot.score_value(value, 2)
