"""RepoPilot Flask application factory."""

from __future__ import annotations

import os


def create_app(test_config: dict | None = None) -> Flask:
    """Create a configured RepoPilot application.

    ``DATABASE_PATH`` (or ``REPOPILOT_DATABASE_PATH``) can point to a test or
    application database.  The default is an instance-local SQLite file.
    """
    # Keep importing ``app.cli`` dependency-free: Flask is only needed when the
    # web application factory is actually used.
    from flask import Flask

    from .history import SnapshotStore
    from .routes import api

    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        JSON_SORT_KEYS=False,
        DATABASE_PATH=os.environ.get("REPOPILOT_DATABASE_PATH") or os.environ.get("REPOPILOT_DATABASE") or os.path.join(app.instance_path, "repopilot.sqlite3"),
    )
    if test_config:
        app.config.update(test_config)
    database_path = app.config.get("DATABASE_PATH")
    if test_config and "DATABASE_PATH" not in test_config and "DATABASE" in test_config:
        database_path = test_config["DATABASE"]
    database_path = database_path or app.config.get("DATABASE")
    app.extensions["repopilot_store"] = SnapshotStore(database_path)
    app.register_blueprint(api)
    return app
