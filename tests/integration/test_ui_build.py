from __future__ import annotations

import base64
from pathlib import Path

import flet as ft

from src.config.settings import load_settings
from src.db.connection import get_connection
from src.db.migrate import run_migrations
from src.interface.dashboard_interface import create_dashboard_interfaces
from src.ui import theme
from src.ui.components.chat_panel import (
    COMPOSER_ATTACH_BUTTON_SIZE,
    COMPOSER_MODEL_SELECTOR_FONT_SIZE,
    COMPOSER_MODEL_SELECTOR_HEIGHT,
    COMPOSER_MODEL_SELECTOR_WIDTH,
    COMPOSER_TOOLBAR_GAP,
    MODEL_ICON_SIZE,
)
from src.ui.components.file_editor import FileEditor
from src.ui.pages.home import (
    COMMAND_EXECUTE_BUTTON_HEIGHT,
    COMMAND_ENTRY_HEIGHT,
    COMMAND_ENTRY_SLOT_HEIGHT,
    COMMAND_EXECUTE_BUTTON_WIDTH,
    COMMAND_HISTORY_PANEL_GAP,
    CONSOLE_DOCK_HEIGHT,
    LOG_SEARCH_ANCHOR_SIZE,
    LOG_SEARCH_FIELD_WIDTH,
    LOG_TOOLBAR_SPACING,
    OpsHomePage,
    WORKSPACE_CHAT_FLEX,
    WORKSPACE_HEIGHT,
    WORKSPACE_LOG_FLEX,
    WORKSPACE_SIDE_FLEX,
)


class PageStub:
    def __init__(self, width: int | None = None, height: int | None = None) -> None:
        self.width = width
        self.height = height

    def update(self, *controls) -> None:
        del controls
        return None


class TaskPageStub(PageStub):
    def __init__(self) -> None:
        self.tasks: list[tuple[object, tuple]] = []

    def run_task(self, handler, *args) -> None:
        self.tasks.append((handler, args))


def test_ops_home_page_builds_control_tree(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    settings = load_settings(
        env_file=tmp_path / ".env",
        environ={"APP_DB_PATH": str(db_path)},
        project_root=tmp_path,
    )
    run_migrations(settings.db_path)
    connection = get_connection(settings.db_path)
    try:
        interfaces = create_dashboard_interfaces(connection, settings)
        control = OpsHomePage(PageStub(), interfaces, settings).build()
    finally:
        connection.close()

    assert control.__class__.__name__ == "Container"
    workspace = control.content.controls[-1]
    assert control.content.scroll is None
    assert workspace.controls[0].height == WORKSPACE_HEIGHT == 600
    assert workspace.controls[0].expand == WORKSPACE_SIDE_FLEX
    assert workspace.controls[1].expand == WORKSPACE_LOG_FLEX
    assert workspace.controls[2].expand == WORKSPACE_CHAT_FLEX
    assert control.content.controls[1].vertical_alignment == ft.CrossAxisAlignment.START
    metric_cards = control.content.controls[1].controls
    assert [card.expand for card in metric_cards] == [1, 1, 1, 1]


def test_log_search_overlay_anchors_to_search_icon_and_toggles(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    settings = load_settings(
        env_file=tmp_path / ".env",
        environ={"APP_DB_PATH": str(db_path)},
        project_root=tmp_path,
    )
    run_migrations(settings.db_path)
    connection = get_connection(settings.db_path)
    try:
        interfaces = create_dashboard_interfaces(connection, settings)
        home = OpsHomePage(PageStub(), interfaces, settings)
        home._build_log_workbench()
    finally:
        connection.close()

    toolbar_stack = home.log_filter_container.content.controls[1].content
    toolbar_row = toolbar_stack.controls[0]
    search_anchor = toolbar_row.controls[1]
    overlay = toolbar_stack.controls[1]
    overlay_row = overlay.content
    overlay_icon_anchor = overlay_row.controls[0]

    assert overlay is home._log_search_overlay
    assert toolbar_row.spacing == LOG_TOOLBAR_SPACING
    assert search_anchor.width == LOG_SEARCH_ANCHOR_SIZE
    assert overlay.left == home.log_viewer.filter_row_width + LOG_TOOLBAR_SPACING
    assert overlay.width == LOG_SEARCH_FIELD_WIDTH
    assert overlay.bgcolor == theme.INPUT_BG
    assert overlay_icon_anchor.width == LOG_SEARCH_ANCHOR_SIZE
    assert overlay_icon_anchor.height == LOG_SEARCH_ANCHOR_SIZE
    assert overlay_icon_anchor.content.tooltip is None
    assert overlay_row.controls[1] is home.log_search
    assert home.log_search.width == LOG_SEARCH_FIELD_WIDTH - LOG_SEARCH_ANCHOR_SIZE

    search_anchor.content.on_click(None)
    assert home._log_search_expanded is True
    assert overlay.visible is True

    overlay_icon_anchor.content.on_click(None)
    assert home._log_search_expanded is False
    assert overlay.visible is False


def test_ops_home_page_recomputes_workspace_for_wide_resize(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    settings = load_settings(
        env_file=tmp_path / ".env",
        environ={"APP_DB_PATH": str(db_path)},
        project_root=tmp_path,
    )
    run_migrations(settings.db_path)
    connection = get_connection(settings.db_path)
    try:
        interfaces = create_dashboard_interfaces(connection, settings)
        home = OpsHomePage(PageStub(width=1400, height=900), interfaces, settings)
        home.build()
    finally:
        connection.close()

    home.apply_responsive_layout(update=False)
    chat_panel = home._chat_workspace_container.content
    chat_footer = chat_panel.content.controls[-1]

    assert home._chat_workspace_container.height == 695
    assert home._log_workbench_container.height == 695
    assert chat_footer.padding.right == 11
    assert home.chat_panel._bubble_width == 404
    assert home._chat_panel_composer_width() == 428
    assert home.chat_panel._composer_row.width == 428
    assert home.chat_panel._input_stack.width == 428
    assert home.chat_panel._draft_attachment_area.width == 420
    assert home.chat_panel._draft_attachment_list.width == 420
    assert home.chat_panel._input.width == 420
    assert home.chat_panel._input.border == ft.InputBorder.NONE
    assert home.chat_panel._input.bgcolor == "#00000000"
    assert home.chat_panel._attach_button_slot.width == COMPOSER_ATTACH_BUTTON_SIZE
    assert home.chat_panel._attach_button.disabled is True
    assert home.chat_panel._model_selector_slot.width == COMPOSER_MODEL_SELECTOR_WIDTH
    assert home.chat_panel._model_selector_slot.height == COMPOSER_MODEL_SELECTOR_HEIGHT
    assert home.chat_panel._model_selector_slot.left == (
        COMPOSER_ATTACH_BUTTON_SIZE + COMPOSER_TOOLBAR_GAP
    )
    assert home.chat_panel._model_selector_slot.right is None
    assert home.chat_panel._model_selector_slot in home.chat_panel._input_stack.controls
    assert home.chat_panel._input_stack.controls.index(
        home.chat_panel._model_selector_slot
    ) < home.chat_panel._input_stack.controls.index(home.chat_panel._send_button_slot)
    assert home.chat_panel._model_selector_button.items
    assert home.chat_panel._model_selector_button.content.width == COMPOSER_MODEL_SELECTOR_WIDTH
    assert home.chat_panel._model_selector_button.content.height == COMPOSER_MODEL_SELECTOR_HEIGHT
    selector_label = home.chat_panel._model_selector_button.content.content.controls[1]
    assert selector_label.value == "DeepSeek v4 Flash"
    assert selector_label.size == COMPOSER_MODEL_SELECTOR_FONT_SIZE
    selector_icon = home.chat_panel._model_selector_button.content.content.controls[0]
    menu_icon = home.chat_panel._model_selector_button.items[0].content.controls[0]
    assert isinstance(selector_icon, ft.Image)
    assert isinstance(menu_icon, ft.Image)
    assert selector_icon.width == selector_icon.height == MODEL_ICON_SIZE
    assert menu_icon.width == menu_icon.height == MODEL_ICON_SIZE
    selector_svg = base64.b64decode(selector_icon.src.split(",", 1)[1]).decode("utf-8")
    menu_svg = base64.b64decode(menu_icon.src.split(",", 1)[1]).decode("utf-8")
    assert 'viewBox="0 0 1024 1024"' in selector_svg
    assert f'width="{MODEL_ICON_SIZE}" height="{MODEL_ICON_SIZE}"' in selector_svg
    assert 'viewBox="0 0 1024 1024"' in menu_svg
    assert f'width="{MODEL_ICON_SIZE}" height="{MODEL_ICON_SIZE}"' in menu_svg
    assert home.chat_panel._send_button.width > home.chat_panel._send_button.height


def test_console_history_is_title_button_overlay_not_permanent_dock_row(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    settings = load_settings(
        env_file=tmp_path / ".env",
        environ={"APP_DB_PATH": str(db_path)},
        project_root=tmp_path,
    )
    run_migrations(settings.db_path)
    connection = get_connection(settings.db_path)
    try:
        interfaces = create_dashboard_interfaces(connection, settings)
        home = OpsHomePage(PageStub(), interfaces, settings)
        log_workbench = home._build_log_workbench()
    finally:
        connection.close()

    stack = log_workbench.content
    dock = stack.controls[0].controls[-1].content
    dock_column = dock.content
    title_controls = dock_column.controls[0].controls[0].controls
    command_entry_slot = dock_column.controls[1]
    command_entry = command_entry_slot.content
    command_row = command_entry.content
    execute_button = command_row.controls[1]

    assert title_controls[2] is home.command_console.history_button
    assert stack.controls[1] is home.command_console.history_panel
    assert len(dock_column.controls) == 2
    assert stack.controls[1].bottom == CONSOLE_DOCK_HEIGHT + COMMAND_HISTORY_PANEL_GAP
    assert stack.controls[1].left == 12
    assert dock.height == CONSOLE_DOCK_HEIGHT
    assert command_entry_slot.height == COMMAND_ENTRY_SLOT_HEIGHT
    assert COMMAND_ENTRY_SLOT_HEIGHT == 52
    assert command_entry.height == COMMAND_ENTRY_HEIGHT
    assert COMMAND_ENTRY_HEIGHT == 44
    assert command_entry.bgcolor == theme.INPUT_BG
    assert command_row.spacing == 0
    assert command_row.controls[0] is home.command_console.command_input
    assert home.command_console.command_input.height == COMMAND_ENTRY_HEIGHT
    assert home.command_console.command_input.text_vertical_align == 0
    assert home.command_console.command_input.border == ft.InputBorder.NONE
    assert home.command_console.command_input.bgcolor == "#00000000"
    assert execute_button.width == COMMAND_EXECUTE_BUTTON_WIDTH
    assert execute_button.height == COMMAND_EXECUTE_BUTTON_HEIGHT
    assert execute_button.height == COMMAND_ENTRY_HEIGHT
    assert execute_button.width > execute_button.height
    assert execute_button.bgcolor == theme.BLUE
    assert execute_button.content.icon == ft.Icons.PLAY_ARROW
    radius = execute_button.border_radius
    assert radius.top_left == 0
    assert radius.bottom_left == 0
    assert radius.top_right == 7
    assert radius.bottom_right == 7


def test_editable_non_config_file_opens_in_code_workbench(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    settings = load_settings(
        env_file=tmp_path / ".env",
        environ={"APP_DB_PATH": str(db_path)},
        project_root=tmp_path,
    )
    settings.mc_server_dir.mkdir(parents=True)
    (settings.mc_server_dir / "notes.txt").write_text("hello\n", encoding="utf-8")
    run_migrations(settings.db_path)
    connection = get_connection(settings.db_path)
    try:
        interfaces = create_dashboard_interfaces(connection, settings)
        home = OpsHomePage(PageStub(), interfaces, settings)

        home._on_file_tree_select("notes.txt")
    finally:
        connection.close()

    assert home.open_tabs["notes.txt"]["kind"] == "workbench"
    assert home.code_workbench._editable is True
    assert home.code_workbench._managed_config is False
    assert home.code_workbench._main_area.bgcolor == theme.PANEL
    assert home.code_workbench._panel_overlay.visible is False
    assert home.code_workbench._audit_button.visible is False
    assert isinstance(home.code_workbench._editors["notes.txt"], FileEditor)
    assert home.code_workbench._save_button.visible is True
    assert home.code_workbench._save_button.disabled is True
    assert home.code_workbench._status_feedback_sep.visible is False


def test_versioned_json_config_shows_version_and_diff_tools_without_proposal_audit(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    settings = load_settings(
        env_file=tmp_path / ".env",
        environ={"APP_DB_PATH": str(db_path)},
        project_root=tmp_path,
    )
    settings.mc_server_dir.mkdir(parents=True)
    (settings.mc_server_dir / "ops.json").write_text("[]\n", encoding="utf-8")
    run_migrations(settings.db_path)
    connection = get_connection(settings.db_path)
    try:
        interfaces = create_dashboard_interfaces(connection, settings)
        home = OpsHomePage(PageStub(), interfaces, settings)

        home._on_file_tree_select("ops.json")
        editor = home.code_workbench._editors["ops.json"]
        editor._editor.value = '[{"name":"Aiden233","level":4}]'
        editor._on_text_change(None)
        assert home.code_workbench._save_button.disabled is False
        home.code_workbench._save_button.on_click(None)
        history = interfaces.version.list_history("ops.json")
        audits = interfaces.file.list_edit_audits("ops.json")
    finally:
        connection.close()

    assert home.code_workbench._editable is True
    assert home.code_workbench._managed_config is False
    assert home.code_workbench._versioned is True
    assert home.code_workbench._audit_button.visible is True
    assert home.code_workbench._version_button.visible is True
    assert home.code_workbench._diff_button.visible is True
    assert isinstance(editor, FileEditor)
    assert home.code_workbench._save_button.visible is True
    assert home.code_workbench._status_feedback_sep.visible is True
    assert "保存" in home.code_workbench._status_feedback.value
    home.code_workbench._audit_button.on_click(None)
    assert home.code_workbench._panel_overlay.visible is True
    assert home.code_workbench._panel_title.value == "审计记录"
    assert len(history) == 2
    assert audits[0]["status"] == "saved"
    assert audits[0]["backup_path"] is None
    assert list(settings.mc_server_dir.glob("ops.json.*.bak")) == []


def test_config_proposal_opens_diff_review_in_workbench_and_can_be_accepted(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    settings = load_settings(
        env_file=tmp_path / ".env",
        environ={"APP_DB_PATH": str(db_path)},
        project_root=tmp_path,
    )
    settings.mc_server_dir.mkdir(parents=True)
    (settings.mc_server_dir / "server.properties").write_text(
        "# server\nmotd=Test\nmax-players=10\n",
        encoding="utf-8",
    )
    run_migrations(settings.db_path)
    connection = get_connection(settings.db_path)
    try:
        interfaces = create_dashboard_interfaces(connection, settings)
        page = TaskPageStub()
        home = OpsHomePage(page, interfaces, settings)
        proposal = interfaces.config.propose_change(
            "server.properties",
            [{"key": "max-players", "value": "20"}],
            "把最大人数改成 20",
            session_id=home.chat_session_id,
        )

        home._on_config_proposal_created(proposal)

        assert home.active_tab == "server.properties"
        assert home.code_workbench._review_bar.visible is True
        assert home.code_workbench._pending_proposal["proposal_id"] == proposal["proposal_id"]
        assert home.code_workbench._panel_overlay.visible is False
        assert home.code_workbench._audit_button.visible is True
        assert home.code_workbench._review_viewer is not None
        assert "max-players=20" in home.code_workbench._review_viewer.content
        changes = [
            (line.marker, line.text)
            for line in home.code_workbench._review_viewer.inline_lines
            if line.marker != " "
        ]
        assert changes == [("-", "max-players=10"), ("+", "max-players=20")]
        assert home.code_workbench._review_viewer.first_change_scroll_key == "inline-change-2"
        assert len(page.tasks) == 1
        home.code_workbench._on_config_feedback = None
        home.code_workbench._accept_button.on_click(None)
        assert home.code_workbench._review_bar.visible is False
        assert home.code_workbench._review_viewer is None
        assert "max-players=20" in (
            settings.mc_server_dir / "server.properties"
        ).read_text(encoding="utf-8")
        assert list(settings.mc_server_dir.glob("server.properties.*.bak")) == []
        home.code_workbench._audit_button.on_click(None)
        assert home.code_workbench._panel_overlay.visible is True
        assert home.code_workbench._panel_title.value == "审计记录"
        home.code_workbench._audit_button.on_click(None)
        assert home.code_workbench._panel_overlay.visible is False

        rejected = interfaces.config.propose_change(
            "server.properties",
            [{"key": "max-players", "value": "30"}],
            "把最大人数改成 30",
            session_id=home.chat_session_id,
        )
        home._on_config_proposal_created(rejected)
        assert home.code_workbench._reject_button.disabled is False
        assert "max-players=30" in home.code_workbench._review_viewer.content
        home.code_workbench._reject_button.on_click(None)
        assert home.code_workbench._review_bar.visible is False
        assert "max-players=20" in (
            settings.mc_server_dir / "server.properties"
        ).read_text(encoding="utf-8")

        conflicted = interfaces.config.propose_change(
            "server.properties",
            [{"key": "max-players", "value": "40"}],
            "把最大人数改成 40",
            session_id=home.chat_session_id,
        )
        home._on_config_proposal_created(conflicted)
        (settings.mc_server_dir / "server.properties").write_text(
            "max-players=25\n",
            encoding="utf-8",
        )
        home.code_workbench._accept_button.on_click(None)
        assert home.code_workbench._review_bar.visible is False
        assert "max-players=25" in (
            settings.mc_server_dir / "server.properties"
        ).read_text(encoding="utf-8")
    finally:
        connection.close()
