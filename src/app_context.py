from __future__ import annotations

from dataclasses import dataclass
from sqlite3 import Connection

from src.config.settings import Settings, load_settings
from src.db.connection import get_connection
from src.db.migrate import run_migrations


@dataclass(frozen=True)
class AppContext:
    settings: Settings
    connection: Connection


def create_app_context() -> AppContext:
    settings = load_settings()
    run_migrations(settings.db_path)
    return AppContext(settings=settings, connection=get_connection(settings.db_path))
