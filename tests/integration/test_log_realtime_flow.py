from __future__ import annotations

import time
from pathlib import Path

from tests.fixtures.fake_mc_server import create_fake_mc_server
from src.db.connection import get_connection
from src.db.migrate import run_migrations
from src.repositories.event_repository import EventRepository
from src.service.log_service import LogService, parse_log_line
from src.interface.log_interface import LogInterface


def test_tail_new_events_finds_new_log_lines(tmp_path: Path) -> None:
    server_dir = tmp_path / "mc_server"
    server_dir.mkdir()
    create_fake_mc_server(server_dir)
    log_path = server_dir / "logs" / "latest.log"

    db_path = tmp_path / "app.db"
    run_migrations(str(db_path))
    conn = get_connection(str(db_path))
    try:
        service = LogService(EventRepository(conn), log_path)
        interface = LogInterface(service)

        events = interface.load_recent_events(limit=100)
        assert len(events) >= 3

        with log_path.open("a", encoding="utf-8") as fh:
            fh.write("[13:07:00 ERROR]: New error appeared\n")
            fh.flush()

        new_events = interface.tail_new_logs()
        assert len(new_events) >= 1
        messages = [e["message"] for e in new_events]
        assert any("New error" in m for m in messages)

        visible = interface.get_visible_events(level="ERROR", limit=100)
        assert len(visible) > 0
    finally:
        conn.close()


def test_initial_log_load_preserves_file_order_across_midnight(tmp_path: Path) -> None:
    log_path = tmp_path / "latest.log"
    log_path.write_text(
        "[23:59:59 INFO]: Before midnight\n[00:00:01 INFO]: After midnight\n",
        encoding="utf-8",
    )
    db_path = tmp_path / "app.db"
    run_migrations(str(db_path))
    conn = get_connection(str(db_path))
    try:
        service = LogService(EventRepository(conn), log_path)

        events = service.load_recent_events(limit=1)

        assert events[0]["message"] == "After midnight"
        assert service.event_repository.latest_id() == 2
    finally:
        conn.close()


def test_ingest_reparses_existing_rows_after_cleaning_rules_change(tmp_path: Path) -> None:
    raw_line = (
        "[275\u67082026 19:07:24.498] [C2ME Storage #8/INFO] "
        "[C2ME Storage/]: Storage ready"
    )
    log_path = tmp_path / "latest.log"
    log_path.write_text(raw_line + "\n", encoding="utf-8")
    db_path = tmp_path / "app.db"
    run_migrations(str(db_path))
    conn = get_connection(str(db_path))
    try:
        repository = EventRepository(conn)
        legacy_event = parse_log_line(raw_line)
        legacy_event["event_time"] = None
        legacy_event["message"] = raw_line
        repository.insert_event(legacy_event)
        service = LogService(repository, log_path)

        assert service.ingest_latest() == 0

        event = repository.list_recent(limit=1)[0]
        assert event["event_time"] == "19:07:24"
        assert event["message"] == "Storage ready"
    finally:
        conn.close()


def test_search_visible_events(tmp_path: Path) -> None:
    server_dir = tmp_path / "mc_server"
    server_dir.mkdir()
    create_fake_mc_server(server_dir)
    log_path = server_dir / "logs" / "latest.log"

    db_path = tmp_path / "app.db"
    run_migrations(str(db_path))
    conn = get_connection(str(db_path))
    try:
        service = LogService(EventRepository(conn), log_path)
        interface = LogInterface(service)
        interface.load_recent_events(limit=100)

        results = interface.get_visible_events(keyword="Test warning")
        assert len(results) >= 1
    finally:
        conn.close()


def test_tail_new_events_can_skip_persistence_for_ui_threads(tmp_path: Path) -> None:
    server_dir = tmp_path / "mc_server"
    server_dir.mkdir()
    create_fake_mc_server(server_dir)
    log_path = server_dir / "logs" / "latest.log"

    db_path = tmp_path / "app.db"
    run_migrations(str(db_path))
    conn = get_connection(str(db_path))
    try:
        service = LogService(EventRepository(conn), log_path)
        interface = LogInterface(service)
        interface.prime_tail_to_end()

        with log_path.open("a", encoding="utf-8") as fh:
            fh.write("[13:08:00 INFO]: Non persisted live line\n")
            fh.flush()

        new_events = interface.tail_new_logs(persist=False)

        assert any("Non persisted live line" in event["message"] for event in new_events)
        assert interface.get_visible_events(keyword="Non persisted live line") == []
    finally:
        conn.close()


def test_no_log_file_returns_empty_gracefully(tmp_path: Path) -> None:
    log_path = tmp_path / "nonexistent" / "latest.log"
    db_path = tmp_path / "app.db"
    run_migrations(str(db_path))
    conn = get_connection(str(db_path))
    try:
        service = LogService(EventRepository(conn), log_path)
        interface = LogInterface(service)
        events = interface.load_recent_events(limit=100)
        assert events is not None
        assert len(events) >= 0
    finally:
        conn.close()


def test_log_cursor_returns_only_events_added_after_operation_started(tmp_path: Path) -> None:
    server_dir = tmp_path / "mc_server"
    server_dir.mkdir()
    create_fake_mc_server(server_dir)
    log_path = server_dir / "logs" / "latest.log"
    db_path = tmp_path / "app.db"
    run_migrations(str(db_path))
    conn = get_connection(str(db_path))
    try:
        service = LogService(EventRepository(conn), log_path)

        cursor = service.capture_cursor()
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write("[00:00:01 INFO]: Command output after midnight\n")

        events = service.list_since(cursor)

        assert [event["message"] for event in events] == [
            "Command output after midnight"
        ]
    finally:
        conn.close()
