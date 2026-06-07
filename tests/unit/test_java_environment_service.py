from __future__ import annotations

from pathlib import Path
import hashlib
import io
import shutil
import urllib.error
import zipfile
from unittest.mock import patch

from src.config.settings import Settings
from src.db.connection import get_connection
from src.db.migrate import run_migrations
from src.repositories.app_settings_repository import AppSettingsRepository
from src.repositories.java_environment_repository import JavaEnvironmentRepository
from src.service.java_environment_service import (
    AdoptiumDownloader,
    JavaCandidate,
    AdoptiumAsset,
    JavaEnvironmentService,
    JavaInstallError,
    MC_JAVA_PATH_OVERRIDE_KEY,
    MC_SERVER_VERSION_OVERRIDE_KEY,
    PortableJavaInstaller,
    java_major_for_minecraft_version,
    parse_java_major,
)
from src.service.server_service import ServerService
from src.repositories.runtime_repository import ServerRuntimeRepository


def _settings(tmp_path: Path, server_dir: Path | None = None) -> Settings:
    root = server_dir or tmp_path / "mc_server"
    root.mkdir(exist_ok=True)
    return Settings(
        app_env="test",
        db_path=tmp_path / "app.db",
        flet_run_view="desktop",
        flet_server_host="127.0.0.1",
        flet_server_port=8550,
        qwen_api_key="",
        qwen_base_url="",
        qwen_model="qwen-plus",
        qwen_timeout_seconds=30,
        qwen_max_tokens=800,
        qwen_temperature=0.2,
        mc_server_dir=root,
        mc_server_jar=root / "server.jar",
        mc_java_path="java",
        mc_java_xms="1G",
        mc_java_xmx="2G",
        mc_extra_args="nogui",
        mc_log_path=root / "logs/latest.log",
        mc_command_mode="stdin",
        mc_rcon_host="127.0.0.1",
        mc_rcon_port=25575,
        mc_rcon_password="",
        mc_start_timeout_seconds=1,
        mc_stop_timeout_seconds=1,
        qwen_log_model="",
        ai_context_max_chars=24000,
        ai_recent_messages_limit=16,
        ai_summary_trigger_messages=24,
        ai_summary_target_chars=3000,
        ai_log_raw_max_chars=60000,
        ai_log_compressed_max_chars=8000,
        ai_stream_enabled=True,
        mc_file_preview_max_bytes=1048576,
        mc_file_tree_max_depth=32,
        mc_editable_file_max_bytes=1048576,
        mc_config_backup_on_save=True,
        java_auto_install_dir=tmp_path / "data/java",
    )


def _repos(tmp_path: Path):
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    connection = get_connection(db_path)
    return (
        connection,
        AppSettingsRepository(connection),
        JavaEnvironmentRepository(connection),
    )


def _java(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    return path


def test_java_major_for_minecraft_version_uses_fallback_table() -> None:
    assert java_major_for_minecraft_version("1.20.5") == 21
    assert java_major_for_minecraft_version("1.20.1") == 17
    assert java_major_for_minecraft_version("1.17.1") == 16
    assert java_major_for_minecraft_version("1.16.5") == 8
    assert java_major_for_minecraft_version("1.11.2") is None


def test_parse_java_major_supports_legacy_and_modern_versions() -> None:
    assert parse_java_major('java version "1.8.0_402"') == 8
    assert parse_java_major('openjdk version "17.0.10" 2024-01-16') == 17
    assert parse_java_major('openjdk version "21.0.2" 2024-01-16') == 21


def test_check_uses_server_core_filename_and_selects_exact_system_java(tmp_path: Path) -> None:
    connection, app_settings, audit_repo = _repos(tmp_path)
    try:
        settings = _settings(tmp_path)
        settings.mc_server_jar.rename(settings.mc_server_dir / "paper-1.20.1.jar") if settings.mc_server_jar.exists() else None
        settings = _settings(tmp_path, settings.mc_server_dir)
        object.__setattr__(settings, "mc_server_jar", settings.mc_server_dir / "paper-1.20.1.jar")
        settings.mc_server_jar.write_bytes(b"fake")
        java8 = _java(tmp_path / "java8/bin/java")
        java17 = _java(tmp_path / "java17/bin/java")

        def runner(path: Path) -> str | None:
            if path == java8:
                return 'openjdk version "1.8.0_402"'
            if path == java17:
                return 'openjdk version "17.0.10"'
            return None

        service = JavaEnvironmentService(
            settings,
            audit_repo,
            app_settings,
            version_runner=runner,
            environ={"PATH": f"{java8.parent}:{java17.parent}"},
        )

        result = service.check_environment()

        assert result["status"] == "selected"
        assert result["minecraft_version"] == "1.20.1"
        assert result["required_java_major"] == 17
        assert result["selected_java"]["java_path"] == str(java17)
        assert audit_repo.list_recent(1)[0]["action"] == "check"
    finally:
        connection.close()


def test_ensure_installs_when_no_exact_java_and_persists_override(tmp_path: Path) -> None:
    connection, app_settings, audit_repo = _repos(tmp_path)
    try:
        settings = _settings(tmp_path)
        installed = JavaCandidate(
            java_path=tmp_path / "data/java/temurin-21/bin/java",
            java_home=tmp_path / "data/java/temurin-21",
            major=21,
            version_text='openjdk version "21.0.2"',
            source="portable_install",
            portable=True,
        )

        class Installer:
            def install(self, required_major: int, package_preference: str = "jre") -> JavaCandidate:
                assert required_major == 21
                assert package_preference == "jre"
                return installed

        service = JavaEnvironmentService(
            settings,
            audit_repo,
            app_settings,
            installer=Installer(),  # type: ignore[arg-type]
            version_runner=lambda _path: None,
            environ={"PATH": ""},
        )

        result = service.ensure_environment(server_version="1.20.5")

        assert result["status"] == "installed"
        assert app_settings.get(MC_JAVA_PATH_OVERRIDE_KEY) == str(installed.java_path)
        assert app_settings.get(MC_SERVER_VERSION_OVERRIDE_KEY) == "1.20.5"
        assert service.effective_settings().mc_java_path == str(installed.java_path)
    finally:
        connection.close()


def test_failed_install_keeps_existing_java_override(tmp_path: Path) -> None:
    connection, app_settings, audit_repo = _repos(tmp_path)
    try:
        settings = _settings(tmp_path)
        app_settings.set(MC_JAVA_PATH_OVERRIDE_KEY, "/existing/java")

        class Installer:
            def install(self, required_major: int, package_preference: str = "jre") -> JavaCandidate:
                raise JavaInstallError("download failed")

        service = JavaEnvironmentService(
            settings,
            audit_repo,
            app_settings,
            installer=Installer(),  # type: ignore[arg-type]
            version_runner=lambda _path: None,
            environ={"PATH": ""},
        )

        result = service.ensure_environment(server_version="1.20.5")

        assert result["status"] == "failed"
        assert app_settings.get(MC_JAVA_PATH_OVERRIDE_KEY) == "/existing/java"
        assert audit_repo.list_recent(1)[0]["status"] == "failed"
    finally:
        connection.close()


def test_portable_installer_verifies_archive_and_installs_java_home(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    archive_path = tmp_path / "temurin.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("jdk-21/bin/java", "#!/bin/sh\n")
    checksum = hashlib.sha256(archive_path.read_bytes()).hexdigest()

    class Downloader:
        def resolve_asset(
            self,
            feature_version: int,
            os_name: str,
            arch: str,
            package_type: str,
        ) -> AdoptiumAsset:
            del os_name, arch
            assert feature_version == 21
            assert package_type == "jre"
            return AdoptiumAsset(
                link=str(archive_path),
                filename="temurin.zip",
                checksum=checksum,
                package_type=package_type,
            )

        def download(self, url: str, target: Path) -> None:
            shutil.copy(Path(url), target)

    installer = PortableJavaInstaller(
        settings,
        version_runner=lambda path: 'openjdk version "21.0.2"' if path.name == "java" else None,
        downloader=Downloader(),  # type: ignore[arg-type]
    )

    candidate = installer.install(21)

    assert candidate.major == 21
    assert candidate.portable is True
    assert candidate.java_home is not None
    assert candidate.java_home.parent == settings.java_auto_install_dir
    assert candidate.java_home.name.startswith("temurin-21-")
    assert candidate.java_path.exists()


def test_adoptium_downloader_uses_stable_binary_url_and_request_headers() -> None:
    payload = b"""[
        {
            "binary": {
                "package": {
                    "link": "https://github.com/adoptium/example.zip",
                    "name": "temurin.zip",
                    "checksum": "abc123"
                }
            }
        }
    ]"""

    with patch("urllib.request.urlopen", return_value=io.BytesIO(payload)) as urlopen:
        asset = AdoptiumDownloader().resolve_asset(21, "windows", "x64", "jre")

    assert asset is not None
    assert asset.link == (
        "https://api.adoptium.net/v3/binary/latest/21/ga/windows/x64/"
        "jre/hotspot/normal/eclipse"
    )
    request = urlopen.call_args.args[0]
    assert request.get_header("User-agent") == "MinecraftServerDashboard/1.0"
    assert request.get_header("Cache-control") == "no-cache"
    assert request.get_header("Accept") == "application/json"


def test_adoptium_downloader_retries_download_once_after_403(tmp_path: Path) -> None:
    error = urllib.error.HTTPError(
        "https://api.adoptium.net/v3/binary/latest/21/ga/windows/x64/jre/"
        "hotspot/normal/eclipse",
        403,
        "Forbidden",
        {},
        None,
    )
    target = tmp_path / "temurin.zip"

    with patch(
        "urllib.request.urlopen",
        side_effect=[error, io.BytesIO(b"archive")],
    ) as urlopen:
        AdoptiumDownloader().download(error.url, target)

    assert urlopen.call_count == 2
    assert target.read_bytes() == b"archive"


def test_unknown_server_version_requests_input(tmp_path: Path) -> None:
    connection, app_settings, audit_repo = _repos(tmp_path)
    try:
        service = JavaEnvironmentService(
            _settings(tmp_path),
            audit_repo,
            app_settings,
            version_runner=lambda _path: None,
            environ={"PATH": ""},
        )

        result = service.check_environment()

        assert result["status"] == "needs_input"
        assert "Minecraft" in result["message"]
    finally:
        connection.close()


def test_server_service_uses_sqlite_java_override_for_effective_settings(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    run_migrations(db_path)
    connection = get_connection(db_path)
    try:
        app_settings = AppSettingsRepository(connection)
        app_settings.set(MC_JAVA_PATH_OVERRIDE_KEY, "/portable/java/bin/java")
        service = ServerService(
            ServerRuntimeRepository(connection),
            _settings(tmp_path),
            app_settings_repository=app_settings,
        )

        assert service._effective_settings().mc_java_path == "/portable/java/bin/java"
    finally:
        connection.close()
