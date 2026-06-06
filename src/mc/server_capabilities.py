from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ServerCore:
    name: str
    version: str | None
    source: str

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "source": self.source,
        }


@dataclass(frozen=True)
class ServerPlugin:
    name: str
    version: str | None
    source: str
    supports_temp_ban: bool = False
    supports_temp_ip_ban: bool = False
    temp_ban_command: str | None = None
    temp_ip_ban_command: str | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "source": self.source,
            "supports_temp_ban": self.supports_temp_ban,
            "supports_temp_ip_ban": self.supports_temp_ip_ban,
            "temp_ban_command": self.temp_ban_command,
            "temp_ip_ban_command": self.temp_ip_ban_command,
        }


@dataclass(frozen=True)
class ServerCapabilities:
    core: ServerCore
    plugins: list[ServerPlugin]
    supports_plugins: bool
    supports_temp_ban: bool
    supports_temp_ip_ban: bool
    temp_ban_command: str | None
    temp_ip_ban_command: str | None
    temp_ban_provider: str | None

    def to_dict(self) -> dict:
        return {
            "server_core": self.core.to_dict(),
            "plugins": [plugin.to_dict() for plugin in self.plugins],
            "supports_plugins": self.supports_plugins,
            "supports_temp_ban": self.supports_temp_ban,
            "supports_temp_ip_ban": self.supports_temp_ip_ban,
            "temp_ban_command": self.temp_ban_command,
            "temp_ip_ban_command": self.temp_ip_ban_command,
            "temp_ban_provider": self.temp_ban_provider,
        }


_CORE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("purpur", "Purpur"),
    ("pufferfish", "Pufferfish"),
    ("paper", "Paper"),
    ("folia", "Folia"),
    ("spigot", "Spigot"),
    ("craftbukkit", "CraftBukkit"),
    ("bukkit", "Bukkit"),
    ("mohist", "Mohist"),
    ("arclight", "Arclight"),
    ("catserver", "CatServer"),
    ("neoforge", "NeoForge"),
    ("forge", "Forge"),
    ("fabric", "Fabric"),
    ("quilt", "Quilt"),
    ("vanilla", "Vanilla"),
)
_PLUGIN_CAPABILITIES = {
    "advancedban": ("tempban", "tempipban"),
    "banmanager": ("tempban", "tempbanip"),
    "essentials": ("tempban", "tempbanip"),
    "essentialsx": ("tempban", "tempbanip"),
    "litebans": ("tempban", "tempipban"),
    "maxbans": ("tempban", "tempipban"),
}
_PLUGIN_METADATA_FILES = ("plugin.yml", "paper-plugin.yml", "bungee.yml")


def detect_server_capabilities(server_dir: Path, server_jar: Path | None = None) -> ServerCapabilities:
    root = server_dir.resolve(strict=False)
    core = detect_server_core(root, server_jar)
    plugins = detect_plugins(root)
    temp_plugin = next((plugin for plugin in plugins if plugin.supports_temp_ban), None)
    temp_ip_plugin = next((plugin for plugin in plugins if plugin.supports_temp_ip_ban), None)
    supports_plugins = bool(plugins) or _core_supports_plugins(core.name) or (root / "plugins").is_dir()

    return ServerCapabilities(
        core=core,
        plugins=plugins,
        supports_plugins=supports_plugins,
        supports_temp_ban=temp_plugin is not None,
        supports_temp_ip_ban=temp_ip_plugin is not None,
        temp_ban_command=temp_plugin.temp_ban_command if temp_plugin else None,
        temp_ip_ban_command=temp_ip_plugin.temp_ip_ban_command if temp_ip_plugin else None,
        temp_ban_provider=temp_plugin.name if temp_plugin else None,
    )


def detect_server_core(server_dir: Path, server_jar: Path | None = None) -> ServerCore:
    candidates = []
    if server_jar is not None:
        candidates.append(server_jar)
    if server_dir.is_dir():
        candidates.extend(sorted(server_dir.glob("*.jar")))

    for jar in candidates:
        name = jar.name.lower()
        for marker, display in _CORE_PATTERNS:
            if marker in name:
                return ServerCore(
                    name=display,
                    version=_version_from_filename(jar.stem, marker),
                    source=jar.name,
                )

    if (server_dir / "plugins").is_dir():
        return ServerCore(name="Bukkit-compatible", version=None, source="plugins/")
    return ServerCore(name="Unknown", version=None, source=str(server_jar or server_dir))


def detect_plugins(server_dir: Path) -> list[ServerPlugin]:
    plugins_dir = server_dir / "plugins"
    if not plugins_dir.is_dir():
        return []

    plugins: list[ServerPlugin] = []
    for jar in sorted(plugins_dir.glob("*.jar")):
        plugins.append(_plugin_from_jar(jar))
    return plugins


def _plugin_from_jar(jar: Path) -> ServerPlugin:
    metadata = _read_plugin_metadata(jar)
    fallback_name = _strip_version_suffix(jar.stem)
    name = metadata.get("name") or fallback_name
    version = metadata.get("version") or _version_from_filename(jar.stem, name)
    commands = {normalize_plugin_key(command) for command in metadata.get("commands", [])}
    temp_ban_command = _first_present(commands, ("tempban",))
    temp_ip_ban_command = _first_present(commands, ("tempbanip", "tempipban"))

    mapped = _PLUGIN_CAPABILITIES.get(normalize_plugin_key(name))
    if temp_ban_command is None and mapped:
        temp_ban_command = mapped[0]
    if temp_ip_ban_command is None and mapped:
        temp_ip_ban_command = mapped[1]

    return ServerPlugin(
        name=name,
        version=version,
        source=f"plugins/{jar.name}",
        supports_temp_ban=temp_ban_command is not None,
        supports_temp_ip_ban=temp_ip_ban_command is not None,
        temp_ban_command=temp_ban_command,
        temp_ip_ban_command=temp_ip_ban_command,
    )


def normalize_plugin_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _read_plugin_metadata(jar: Path) -> dict:
    try:
        with zipfile.ZipFile(jar) as archive:
            for metadata_file in _PLUGIN_METADATA_FILES:
                try:
                    raw = archive.read(metadata_file)
                except KeyError:
                    continue
                return _parse_plugin_metadata(raw.decode("utf-8", errors="replace"))
    except (OSError, zipfile.BadZipFile):
        return {}
    return {}


def _parse_plugin_metadata(content: str) -> dict:
    metadata: dict[str, object] = {"commands": []}
    in_commands = False
    command_indent: int | None = None
    commands: list[str] = []

    for raw_line in content.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        key, separator, value = stripped.partition(":")
        if not separator:
            continue
        normalized_key = key.strip()
        normalized_value = value.strip().strip("\"'")

        if indent == 0:
            in_commands = normalized_key == "commands"
            command_indent = None
            if normalized_key in {"name", "version"} and normalized_value:
                metadata[normalized_key] = normalized_value
            continue

        if in_commands and normalized_key:
            if command_indent is None:
                command_indent = indent
            if indent == command_indent:
                commands.append(normalized_key)

    metadata["commands"] = commands
    return metadata


def _first_present(commands: set[str], candidates: tuple[str, ...]) -> str | None:
    for command in candidates:
        if command in commands:
            return command
    return None


def _strip_version_suffix(value: str) -> str:
    stripped = re.split(r"[-_ ]v?\d", value, maxsplit=1, flags=re.IGNORECASE)[0]
    return stripped or value


def _version_from_filename(value: str, marker: str) -> str | None:
    pattern = re.compile(re.escape(marker), re.IGNORECASE)
    match = pattern.search(value)
    if not match:
        return None
    suffix = value[match.end():].strip("-_ ")
    return suffix or None


def _core_supports_plugins(name: str) -> bool:
    return name in {
        "Arclight",
        "Bukkit",
        "Bukkit-compatible",
        "CatServer",
        "CraftBukkit",
        "Folia",
        "Mohist",
        "Paper",
        "Pufferfish",
        "Purpur",
        "Spigot",
    }
