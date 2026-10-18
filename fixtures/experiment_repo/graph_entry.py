"""Small graph entry point that consumes the dependency hub."""

import dependency_hub


def run(value: int) -> int:
    return dependency_hub.dispatch(value, "healthy")
