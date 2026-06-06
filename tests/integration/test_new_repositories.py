from __future__ import annotations

from pathlib import Path

from src.db.connection import get_connection
from src.db.migrate import run_migrations
from src.repositories.chat_summary_repository import ChatSummaryRepository
from src.repositories.chat_attachment_repository import ChatAttachmentRepository
from src.repositories.log_analysis_repository import LogAnalysisRepository
from src.repositories.file_edit_repository import FileEditAuditRepository
from src.repositories.chat_repository import ChatRepository


def test_chat_summary_crud(tmp_path: Path):
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    conn = get_connection(db_path)

    chat_repo = ChatRepository(conn)
    summary_repo = ChatSummaryRepository(conn)

    session_id = chat_repo.create_session("test")
    summary_id = summary_repo.create(session_id, "一段摘要内容", covered_message_id="msg_1")

    assert summary_id > 0
    latest = summary_repo.get_latest(session_id)
    assert latest is not None
    assert latest["summary"] == "一段摘要内容"
    assert latest["session_id"] == session_id

    conn.close()


def test_chat_attachment_crud(tmp_path: Path):
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    conn = get_connection(db_path)

    chat_repo = ChatRepository(conn)
    attachment_repo = ChatAttachmentRepository(conn)

    session_id = chat_repo.create_session("test")
    attachment_id = attachment_repo.create(
        session_id=session_id,
        kind="log_selection",
        label="日志片段 test.log · 10 行",
        content="[ERROR] Something went wrong\n[WARN] Warning message",
        metadata={"source": "test.log", "line_count": 10},
    )

    assert attachment_id
    attachment = attachment_repo.get(attachment_id)
    assert attachment is not None
    assert attachment["kind"] == "log_selection"
    assert "ERROR" in attachment["content"]

    all_attachments = attachment_repo.list_for_session(session_id)
    assert len(all_attachments) == 1

    conn.close()


def test_log_analysis_repository(tmp_path: Path):
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    conn = get_connection(db_path)

    # Create an attachment first (FK constraint)
    chat_repo = ChatRepository(conn)
    attachment_repo = ChatAttachmentRepository(conn)
    session_id = chat_repo.create_session("test")
    attachment_id = attachment_repo.create(
        session_id=session_id,
        kind="log_selection",
        label="test.log",
        content="[ERROR] test",
    )

    repo = LogAnalysisRepository(conn)
    analysis_id = repo.create(
        source_label="test.log 42行",
        raw_line_count=42,
        raw_char_count=2048,
        compressed_text="压缩后的日志文本",
        key_findings=["发现1", "发现2"],
        retained_snippets=["片段1", "片段2"],
        attachment_id=attachment_id,
        model="fake-model",
    )

    assert analysis_id
    result = repo.get(analysis_id)
    assert result is not None
    assert result["raw_line_count"] == 42
    assert result["source_label"] == "test.log 42行"

    # Test get by attachment
    by_attachment = repo.get_by_attachment(attachment_id)
    assert by_attachment is not None
    assert by_attachment["id"] == analysis_id

    conn.close()


def test_file_edit_audit_repository(tmp_path: Path):
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    conn = get_connection(db_path)

    repo = FileEditAuditRepository(conn)
    audit_id = repo.create(
        relative_path="server.properties",
        size_before=1024,
        size_after=1050,
        status="saved",
        backup_path="server.properties.20260520_180000.bak",
    )

    assert audit_id > 0

    records = repo.list_recent(limit=10)
    assert len(records) > 0
    assert records[0]["relative_path"] == "server.properties"

    path_records = repo.list_for_path("server.properties")
    assert len(path_records) == 1

    conn.close()
