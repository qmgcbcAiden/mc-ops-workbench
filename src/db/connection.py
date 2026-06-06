from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
import sqlite3
import threading
from pathlib import Path


class AppConnection(sqlite3.Connection):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.lock = threading.RLock()


def get_connection(db_path: str | Path) -> AppConnection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(path, check_same_thread=False, factory=AppConnection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def locked_connection(connection: sqlite3.Connection) -> AbstractContextManager:
    lock = getattr(connection, "lock", None)
    if lock is None:
        return nullcontext()
    return lock


def row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)
