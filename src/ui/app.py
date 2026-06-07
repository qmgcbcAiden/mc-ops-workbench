from __future__ import annotations

import os
import threading
from typing import Any, Callable, MutableMapping

import flet as ft

from src.app_context import create_app_context
from src.config.settings import load_settings
from src.interface.dashboard_interface import create_dashboard_interfaces
from src.service.environment_settings_service import EnvironmentSettingsService
from src.ui import theme
from src.ui.pages.home import OpsHomePage


WINDOW_WIDTH = 1200
WINDOW_HEIGHT = 875
WINDOW_MIN_WIDTH = 960
WINDOW_MIN_HEIGHT = 700


class AppShutdownController:
    def __init__(self, home: OpsHomePage, interfaces: Any, context: Any) -> None:
        self._home = home
        self._interfaces = interfaces
        self._context = context
        self._lock = threading.Lock()
        self._done = False

    def replace(self, home: OpsHomePage, interfaces: Any, context: Any) -> None:
        with self._lock:
            if self._done:
                return
            self._home = home
            self._interfaces = interfaces
            self._context = context

    def shutdown(self) -> None:
        with self._lock:
            if self._done:
                return
            self._done = True

        _safe_call(self._home.shutdown)
        _safe_call(lambda: self._interfaces.server.shutdown_server(force=True))
        _safe_call(self._context.connection.close)


def main_view(page: ft.Page, *, first_run: bool = False) -> None:
    context = create_app_context()
    interfaces = create_dashboard_interfaces(context.connection, context.settings)

    page.title = "MC \u8fd0\u7ef4\u5de5\u4f5c\u53f0"
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = theme.BG
    page.padding = 0
    page.window.width = WINDOW_WIDTH
    page.window.height = WINDOW_HEIGHT
    page.window.min_width = WINDOW_MIN_WIDTH
    page.window.min_height = WINDOW_MIN_HEIGHT
    page.window.alignment = ft.Alignment(0, 0)

    runtime: dict[str, Any] = {}

    def on_settings_saved(result: dict) -> None:
        current_home = runtime["home"]
        current_interfaces = runtime["interfaces"]
        state = current_interfaces.server.get_server_status().get("state", "stopped")
        if state not in {"stopped", "crashed"}:
            current_home.show_notification(
                "设置已保存。服务器正在运行，新配置将在下次启动应用后生效。",
            )
            return

        new_context = None
        try:
            new_context = create_app_context()
            new_interfaces = create_dashboard_interfaces(
                new_context.connection,
                new_context.settings,
            )
            new_home = build_home(new_context, new_interfaces)
            new_control = new_home.build()
        except Exception as exc:
            if new_context is not None:
                _safe_call(new_context.connection.close)
            current_home.show_notification(
                f"设置已保存，但重载失败：{exc}",
                is_error=True,
            )
            return

        current_home.shutdown()
        _safe_call(runtime["context"].connection.close)
        page.clean()
        page.add(new_control)
        runtime.update(
            context=new_context,
            interfaces=new_interfaces,
            home=new_home,
        )
        shutdown_controller.replace(new_home, new_interfaces, new_context)
        page.on_resize = lambda _event=None: new_home.apply_responsive_layout()
        page.on_disconnect = lambda _event=None: shutdown_controller.shutdown()
        new_home.start_background_refresh()
        new_home.show_notification(result.get("message") or "设置已保存并生效。")

    def build_home(current_context, current_interfaces) -> OpsHomePage:
        return OpsHomePage(
            page,
            current_interfaces,
            current_context.settings,
            on_settings_saved=on_settings_saved,
        )

    home = build_home(context, interfaces)
    runtime.update(context=context, interfaces=interfaces, home=home)
    shutdown_controller = AppShutdownController(home, interfaces, context)

    async def destroy_window() -> None:
        try:
            page.window.prevent_close = False
        except Exception:
            pass
        try:
            await page.window.destroy()
        except Exception:
            try:
                await page.window.close()
            except Exception:
                pass

    def on_window_event(event) -> None:
        if not _is_window_close_event(event):
            return
        shutdown_controller.shutdown()
        try:
            page.window.prevent_close = False
        except Exception:
            pass
        try:
            page.run_task(destroy_window)
        except Exception:
            pass

    page.window.prevent_close = True
    page.window.on_event = on_window_event
    page.on_resize = lambda _event=None: home.apply_responsive_layout()
    page.on_disconnect = lambda _event=None: shutdown_controller.shutdown()

    page.add(home.build())
    _center_window(page)
    home.start_background_refresh()
    if first_run:
        home.show_settings(first_run=True)


def _safe_call(callback: Callable[[], None]) -> None:
    try:
        callback()
    except Exception:
        pass


def _center_window(page: ft.Page) -> None:
    async def center_window() -> None:
        try:
            await page.window.center()
        except Exception:
            pass

    run_task = getattr(page, "run_task", None)
    if callable(run_task):
        try:
            run_task(center_window)
        except Exception:
            pass


def _is_window_close_event(event) -> bool:
    event_type = getattr(event, "type", None)
    event_value = getattr(event_type, "value", event_type)
    if event_value == "close":
        return True
    return getattr(event, "data", None) == "close"


def run() -> None:
    bootstrap = EnvironmentSettingsService.ensure_environment_file()
    settings = load_settings()
    run_view = settings.flet_run_view.strip().lower()

    if run_view == "desktop":
        _configure_ssl_cert_file_for_desktop_bootstrap()
        ft.run(
            lambda page: main_view(page, first_run=bootstrap.created),
            view=ft.AppView.FLET_APP,
        )
        return

    ft.run(
        lambda page: main_view(page, first_run=bootstrap.created),
        view=ft.AppView.WEB_BROWSER,
        host=settings.flet_server_host,
        port=settings.flet_server_port,
    )


def _configure_ssl_cert_file_for_desktop_bootstrap(
    environ: MutableMapping[str, str] | None = None,
) -> None:
    env = os.environ if environ is None else environ
    if env.get("SSL_CERT_FILE"):
        return

    try:
        import certifi
    except ImportError:
        return

    ca_bundle = certifi.where()
    env["SSL_CERT_FILE"] = ca_bundle
    env.setdefault("REQUESTS_CA_BUNDLE", ca_bundle)


if __name__ == "__main__":
    run()
