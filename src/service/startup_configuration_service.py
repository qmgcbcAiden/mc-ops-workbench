from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.config.settings import Settings
from src.mc.properties_parser import parse_properties


@dataclass(frozen=True)
class StartupConfigurationResult:
    properties_path: Path
    changed_keys: tuple[str, ...]


class StartupConfigurationError(RuntimeError):
    pass


class StartupConfigurationService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def ensure_rcon_configuration(self) -> StartupConfigurationResult:
        properties_path = self._settings.mc_server_dir / "server.properties"
        password = self._settings.mc_rcon_password
        if not password:
            raise StartupConfigurationError(
                "MC_RCON_PASSWORD 未配置，无法自动启用安全的 RCON 连接。"
            )

        try:
            content = properties_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            content = ""
        except (OSError, UnicodeError) as exc:
            raise StartupConfigurationError(
                "无法读取 server.properties，请检查文件权限和编码。"
            ) from exc

        document = parse_properties(content)
        required_values = (
            ("enable-rcon", "true"),
            ("rcon.password", password),
            ("rcon.port", str(self._settings.mc_rcon_port)),
        )
        changed_keys: list[str] = []
        for key, value in required_values:
            if document.get(key) != value:
                document.set(key, value)
                changed_keys.append(key)

        if changed_keys:
            try:
                properties_path.parent.mkdir(parents=True, exist_ok=True)
                properties_path.write_text(document.to_text(), encoding="utf-8")
            except OSError as exc:
                raise StartupConfigurationError(
                    "无法写入 server.properties，请检查文件权限。"
                ) from exc

        return StartupConfigurationResult(
            properties_path=properties_path,
            changed_keys=tuple(changed_keys),
        )
