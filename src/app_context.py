from __future__ import annotations

from dataclasses import dataclass
from sqlite3 import Connection

from src.config.settings import Settings, load_settings
from src.db.connection import get_connection
from src.db.migrate import run_migrations
from src.service.startup_configuration_service import StartupConfigurationService


@dataclass(frozen=True)
class AppContext:
    settings: Settings
    connection: Connection


def create_app_context() -> AppContext:
    settings = load_settings()
    StartupConfigurationService(settings).ensure_rcon_configuration()
    run_migrations(settings.db_path)
    return AppContext(settings=settings, connection=get_connection(settings.db_path))
