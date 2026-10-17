"""RepoPilot Flask application factory."""

from flask import Flask

from .history import SnapshotStore
from .routes import api


def create_app() -> Flask:
    """Create a configured RepoPilot application.

    The prototype intentionally keeps state in memory. A process restart simply
    returns to the built-in demo repository, which makes local development
    predictable and keeps the backend free of external services.
    """

    app = Flask(__name__)
    app.config.from_mapping(JSON_SORT_KEYS=False)
    app.extensions["repopilot_store"] = SnapshotStore()
    app.register_blueprint(api)
    return app
