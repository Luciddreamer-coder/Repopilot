"""Known unresolved imports kept separate from the resolved graph."""

import dependency_hub
import missing_local_package
import third_party_missing


def run(value: int) -> int:
    return dependency_hub.dispatch(value, "healthy")
