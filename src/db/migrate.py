from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from src.config.settings import load_settings
from src.db.connection import get_connection


MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
MIGRATION_RE = re.compile(r"^(\d+)_.*\.sql$")


def _migration_version(path: Path) -> int:
    match = MIGRATION_RE.match(path.name)
    if not match:
        raise ValueError(f"Invalid migration filename: {path.name}")
    return int(match.group(1))


def list_migrations(migrations_dir: Path = MIGRATIONS_DIR) -> list[tuple[int, Path]]:
    migrations = []
    for path in migrations_dir.glob("*.sql"):
        migrations.append((_migration_version(path), path))
    return sorted(migrations, key=lambda item: item[0])


def get_user_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("PRAGMA user_version").fetchone()
    return int(row[0])


def set_user_version(connection: sqlite3.Connection, version: int) -> None:
    connection.execute(f"PRAGMA user_version = {version}")


def run_migrations(
    db_path: str | Path,
    migrations_dir: Path = MIGRATIONS_DIR,
) -> int:
    connection = get_connection(db_path)
    try:
        current_version = get_user_version(connection)
        applied_version = current_version

        for version, path in list_migrations(migrations_dir):
            if version <= current_version:
                continue

            sql = path.read_text(encoding="utf-8")
            with connection:
                connection.executescript(sql)
                set_user_version(connection, version)
            applied_version = version

        return applied_version
    finally:
        connection.close()


def main() -> None:
    settings = load_settings()
    version = run_migrations(settings.db_path)
    print(f"Database is at schema version {version}: {settings.db_path}")


if __name__ == "__main__":
    main()
