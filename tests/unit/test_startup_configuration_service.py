from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src import app_context
from src.service.startup_configuration_service import (
    StartupConfigurationError,
    StartupConfigurationService,
)


def _settings(server_dir: Path, password: str = "configured-secret") -> SimpleNamespace:
    return SimpleNamespace(
        mc_server_dir=server_dir,
        mc_rcon_password=password,
        mc_rcon_port=25575,
        db_path=server_dir.parent / "app.db",
    )


def test_rcon_configuration_is_not_rewritten_when_it_already_matches_env(tmp_path: Path) -> None:
    server_dir = tmp_path / "mc_server"
    server_dir.mkdir()
    (server_dir / "server.properties").write_text(
        "enable-rcon=true\nrcon.password=configured-secret\nrcon.port=25575\n",
        encoding="utf-8",
    )

    result = StartupConfigurationService(_settings(server_dir)).ensure_rcon_configuration()

    assert result.changed_keys == ()


def test_rcon_configuration_fixes_mismatches_and_preserves_existing_entries(
    tmp_path: Path,
) -> None:
    server_dir = tmp_path / "mc_server"
    server_dir.mkdir()
    properties_path = server_dir / "server.properties"
    properties_path.write_text(
        "# server configuration\nmotd=Keep me\nenable-rcon=false\n"
        "rcon.password=server-secret\nrcon.port=25590\n",
        encoding="utf-8",
    )

    result = StartupConfigurationService(_settings(server_dir)).ensure_rcon_configuration()
    content = properties_path.read_text(encoding="utf-8")

    assert result.changed_keys == ("enable-rcon", "rcon.password", "rcon.port")
    assert "# server configuration\nmotd=Keep me\n" in content
    assert "enable-rcon=true\n" in content
    assert "rcon.password=configured-secret\n" in content
    assert "rcon.port=25575\n" in content


def test_rcon_configuration_creates_missing_properties_file(tmp_path: Path) -> None:
    server_dir = tmp_path / "mc_server"
    result = StartupConfigurationService(_settings(server_dir)).ensure_rcon_configuration()
    content = (server_dir / "server.properties").read_text(encoding="utf-8")

    assert result.changed_keys == ("enable-rcon", "rcon.password", "rcon.port")
    assert "enable-rcon=true" in content
    assert "rcon.password=configured-secret" in content
    assert "rcon.port=25575" in content


def test_rcon_configuration_requires_env_password(tmp_path: Path) -> None:
    with pytest.raises(StartupConfigurationError, match="MC_RCON_PASSWORD"):
        StartupConfigurationService(_settings(tmp_path, password="")).ensure_rcon_configuration()

    assert not (tmp_path / "server.properties").exists()


def test_app_context_configures_rcon_before_database_migration(
    tmp_path: Path, monkeypatch
) -> None:
    server_dir = tmp_path / "mc_server"
    settings = _settings(server_dir)
    migration_calls: list[Path] = []
    monkeypatch.setattr(app_context, "load_settings", lambda: settings)
    monkeypatch.setattr(
        app_context,
        "run_migrations",
        lambda db_path: migration_calls.append(db_path),
    )
    monkeypatch.setattr(app_context, "get_connection", lambda _db_path: object())

    app_context.create_app_context()

    assert "enable-rcon=true" in (server_dir / "server.properties").read_text(encoding="utf-8")
    assert migration_calls == [settings.db_path]
