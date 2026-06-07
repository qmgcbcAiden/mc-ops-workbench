from __future__ import annotations

import asyncio
from types import SimpleNamespace

from src.ui import app as app_module
from src.ui.app import (
    AppShutdownController,
    WINDOW_HEIGHT,
    WINDOW_MIN_HEIGHT,
    WINDOW_MIN_WIDTH,
    WINDOW_WIDTH,
    _configure_ssl_cert_file_for_desktop_bootstrap,
    _is_window_close_event,
)


class _HomeStub:
    def __init__(self) -> None:
        self.shutdown_count = 0

    def shutdown(self) -> None:
        self.shutdown_count += 1


class _ServerStub:
    def __init__(self) -> None:
        self.shutdown_calls: list[bool] = []

    def shutdown_server(self, force: bool = True) -> dict:
        self.shutdown_calls.append(force)
        return {"state": "stopped"}


class _ConnectionStub:
    def __init__(self) -> None:
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1


def test_app_shutdown_controller_stops_everything_once() -> None:
    home = _HomeStub()
    server = _ServerStub()
    connection = _ConnectionStub()
    controller = AppShutdownController(
        home,
        SimpleNamespace(server=server),
        SimpleNamespace(connection=connection),
    )

    controller.shutdown()
    controller.shutdown()

    assert home.shutdown_count == 1
    assert server.shutdown_calls == [True]
    assert connection.close_count == 1


def test_window_close_event_detection_supports_current_and_legacy_flet_events() -> None:
    assert _is_window_close_event(SimpleNamespace(type=SimpleNamespace(value="close")))
    assert _is_window_close_event(SimpleNamespace(type="close"))
    assert _is_window_close_event(SimpleNamespace(data="close"))
    assert not _is_window_close_event(SimpleNamespace(type=SimpleNamespace(value="resize")))


def test_desktop_bootstrap_uses_certifi_when_ssl_cert_file_is_missing() -> None:
    env: dict[str, str] = {}

    _configure_ssl_cert_file_for_desktop_bootstrap(env)

    assert env["SSL_CERT_FILE"].endswith("cacert.pem")
    assert env["REQUESTS_CA_BUNDLE"] == env["SSL_CERT_FILE"]


def test_desktop_bootstrap_preserves_explicit_ssl_cert_file() -> None:
    env = {"SSL_CERT_FILE": "/custom/cert.pem"}

    _configure_ssl_cert_file_for_desktop_bootstrap(env)

    assert env == {"SSL_CERT_FILE": "/custom/cert.pem"}


def test_main_view_starts_background_refresh_after_page_add(monkeypatch) -> None:
    calls: list[str] = []

    class _HomeForMainView:
        def __init__(self, page, interfaces, settings, on_settings_saved=None) -> None:
            del page, interfaces, settings
            self.on_settings_saved = on_settings_saved

        def build(self):
            calls.append("build")
            return object()

        def start_background_refresh(self) -> None:
            calls.append("start_background_refresh")

        def apply_responsive_layout(self) -> None:
            calls.append("resize")

        def shutdown(self) -> None:
            calls.append("shutdown")

        def show_settings(self, first_run: bool = False) -> None:
            calls.append(f"show_settings:{first_run}")

    class _WindowForMainView:
        async def center(self) -> None:
            calls.append("center_window")

    class _PageForMainView:
        def __init__(self) -> None:
            self.window = _WindowForMainView()
            self.added = []

        def add(self, control) -> None:
            self.added.append(control)
            calls.append("page_add")

        def run_task(self, handler, *args) -> None:
            calls.append("run_task")
            asyncio.run(handler(*args))

    monkeypatch.setattr(
        app_module,
        "create_app_context",
        lambda: SimpleNamespace(connection=_ConnectionStub(), settings=SimpleNamespace()),
    )
    monkeypatch.setattr(
        app_module,
        "create_dashboard_interfaces",
        lambda connection, settings: SimpleNamespace(server=_ServerStub()),
    )
    monkeypatch.setattr(app_module, "OpsHomePage", _HomeForMainView)

    page = _PageForMainView()
    app_module.main_view(page)

    assert calls[:5] == [
        "build",
        "page_add",
        "run_task",
        "center_window",
        "start_background_refresh",
    ]
    assert page.window.width == WINDOW_WIDTH == 1200
    assert page.window.height == WINDOW_HEIGHT == 875
    assert page.window.min_width == WINDOW_MIN_WIDTH == 960
    assert page.window.min_height == WINDOW_MIN_HEIGHT == 700
    assert page.window.alignment.x == 0
    assert page.window.alignment.y == 0
    page.on_resize()
    assert calls[-1] == "resize"


def test_stopped_server_settings_save_reloads_workbench_in_place(monkeypatch) -> None:
    calls: list[str] = []
    old_context = SimpleNamespace(
        connection=_ConnectionStub(),
        settings=SimpleNamespace(name="old"),
    )
    new_context = SimpleNamespace(
        connection=_ConnectionStub(),
        settings=SimpleNamespace(name="new"),
    )
    contexts = [old_context, new_context]
    interfaces = [
        SimpleNamespace(server=SimpleNamespace(get_server_status=lambda: {"state": "stopped"})),
        SimpleNamespace(server=SimpleNamespace(get_server_status=lambda: {"state": "stopped"})),
    ]
    homes = []

    class _ReloadHome:
        def __init__(self, page, current_interfaces, settings, on_settings_saved=None) -> None:
            del page, current_interfaces
            self.settings = settings
            self.on_settings_saved = on_settings_saved
            self.shutdown_count = 0
            self.start_count = 0
            self.messages = []
            homes.append(self)

        def build(self):
            calls.append(f"build:{self.settings.name}")
            return self.settings.name

        def start_background_refresh(self) -> None:
            self.start_count += 1

        def apply_responsive_layout(self) -> None:
            pass

        def shutdown(self) -> None:
            self.shutdown_count += 1

        def show_notification(self, message, *, is_error=False) -> None:
            self.messages.append((message, is_error))

    class _Window:
        async def center(self) -> None:
            pass

    class _Page:
        def __init__(self) -> None:
            self.window = _Window()
            self.added = []
            self.clean_count = 0

        def add(self, control) -> None:
            self.added.append(control)

        def clean(self) -> None:
            self.clean_count += 1

        def run_task(self, handler, *args) -> None:
            asyncio.run(handler(*args))

    monkeypatch.setattr(app_module, "create_app_context", lambda: contexts.pop(0))
    monkeypatch.setattr(
        app_module,
        "create_dashboard_interfaces",
        lambda _connection, _settings: interfaces.pop(0),
    )
    monkeypatch.setattr(app_module, "OpsHomePage", _ReloadHome)
    page = _Page()

    app_module.main_view(page)
    old_home = homes[0]
    old_home.on_settings_saved({"message": "设置已保存。"})

    assert page.clean_count == 1
    assert page.added == ["old", "new"]
    assert old_home.shutdown_count == 1
    assert old_context.connection.close_count == 1
    assert new_context.connection.close_count == 0
    assert homes[1].start_count == 1
    assert homes[1].messages == [("设置已保存。", False)]


def test_running_server_settings_save_does_not_reload(monkeypatch) -> None:
    context = SimpleNamespace(connection=_ConnectionStub(), settings=SimpleNamespace())
    interface = SimpleNamespace(
        server=SimpleNamespace(get_server_status=lambda: {"state": "running"})
    )
    homes = []

    class _RunningHome:
        def __init__(self, page, interfaces, settings, on_settings_saved=None) -> None:
            del page, interfaces, settings
            self.on_settings_saved = on_settings_saved
            self.messages = []
            homes.append(self)

        def build(self):
            return object()

        def start_background_refresh(self) -> None:
            pass

        def apply_responsive_layout(self) -> None:
            pass

        def shutdown(self) -> None:
            pass

        def show_notification(self, message, *, is_error=False) -> None:
            self.messages.append((message, is_error))

    class _Window:
        async def center(self) -> None:
            pass

    class _Page:
        def __init__(self) -> None:
            self.window = _Window()
            self.add_count = 0

        def add(self, _control) -> None:
            self.add_count += 1

        def run_task(self, handler, *args) -> None:
            asyncio.run(handler(*args))

    monkeypatch.setattr(app_module, "create_app_context", lambda: context)
    monkeypatch.setattr(
        app_module,
        "create_dashboard_interfaces",
        lambda _connection, _settings: interface,
    )
    monkeypatch.setattr(app_module, "OpsHomePage", _RunningHome)
    page = _Page()

    app_module.main_view(page)
    homes[0].on_settings_saved({"message": "设置已保存。"})

    assert page.add_count == 1
    assert homes[0].messages == [
        ("设置已保存。服务器正在运行，新配置将在下次启动应用后生效。", False)
    ]
