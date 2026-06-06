from __future__ import annotations

import hashlib
import json
import re
import tomllib
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.mc.properties_parser import parse_properties
from src.mc.server_capabilities import ServerCore, detect_server_core
from src.mc.server_files import resolve_inside_root


SEVERITIES = ("BLOCKER", "HIGH", "MEDIUM", "LOW", "INFO")
HARD_EVIDENCE_TYPES = {"metadata", "dependency_graph", "api_hash_match", "runtime_log"}
HEURISTIC_EVIDENCE_TYPE = "heuristic_filename"
PSEUDO_DEPENDENCIES = {
    "minecraft",
    "java",
    "fabricloader",
    "fabric-loader",
    "quiltloader",
    "quilt_loader",
    "forge",
    "neoforge",
    "modloader",
}

_METADATA_FILES = (
    "fabric.mod.json",
    "quilt.mod.json",
    "META-INF/mods.toml",
    "META-INF/neoforge.mods.toml",
    "plugin.yml",
    "paper-plugin.yml",
    "bungee.yml",
)
_PLUGIN_METADATA_FILES = {"plugin.yml", "paper-plugin.yml", "bungee.yml"}


@dataclass
class AddonDependency:
    addon_id: str
    required: bool = True
    version_range: str | None = None
    side: str | None = None
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "addon_id": self.addon_id,
            "required": self.required,
            "version_range": self.version_range,
            "side": self.side,
            "source": self.source,
        }


@dataclass
class AddonAsset:
    relative_path: str
    file_name: str
    folder: str
    kind: str
    addon_id: str | None
    name: str | None
    version: str | None
    loader: str | None
    environment: str | None
    minecraft_versions: list[str] = field(default_factory=list)
    dependencies: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    sha1: str | None = None
    sha512: str | None = None
    file_size_bytes: int = 0
    metadata_source: str | None = None
    metadata_confidence: str = "heuristic"
    knowledge: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "file_name": self.file_name,
            "folder": self.folder,
            "kind": self.kind,
            "addon_id": self.addon_id,
            "name": self.name,
            "version": self.version,
            "loader": self.loader,
            "environment": self.environment,
            "minecraft_versions": list(self.minecraft_versions),
            "dependencies": list(self.dependencies),
            "conflicts": list(self.conflicts),
            "sha1": self.sha1,
            "sha512": self.sha512,
            "file_size_bytes": self.file_size_bytes,
            "metadata_source": self.metadata_source,
            "metadata_confidence": self.metadata_confidence,
            "knowledge": dict(self.knowledge),
        }


@dataclass
class AddonDiagnostic:
    severity: str
    category: str
    message: str
    evidence_type: str
    confidence: str
    affected_files: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    sources: list[dict[str, str]] = field(default_factory=list)
    suggested_actions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        severity = normalize_severity(self.severity, self.evidence_type)
        return {
            "severity": severity,
            "category": self.category,
            "message": self.message,
            "evidence_type": self.evidence_type,
            "confidence": self.confidence,
            "affected_files": list(self.affected_files),
            "evidence": dict(self.evidence),
            "sources": list(self.sources),
            "suggested_actions": list(self.suggested_actions),
        }


def scan_local_addons(
    server_dir: Path,
    server_jar: Path | None = None,
    max_jar_bytes: int = 104_857_600,
) -> tuple[list[AddonAsset], ServerCore]:
    root = server_dir.resolve(strict=False)
    core = detect_server_core(root, server_jar)
    assets: list[AddonAsset] = []
    for folder in ("mods", "plugins"):
        directory = resolve_inside_root(root, folder)
        if not directory.is_dir():
            continue
        for jar_path in sorted(directory.glob("*.jar")):
            assets.append(scan_addon_jar(root, jar_path, max_jar_bytes=max_jar_bytes))
    return assets, core


def scan_addon_jar(root: Path, jar_path: Path, max_jar_bytes: int) -> AddonAsset:
    relative_path = jar_path.resolve(strict=False).relative_to(root.resolve(strict=False)).as_posix()
    folder = Path(relative_path).parts[0] if Path(relative_path).parts else ""
    size = _file_size(jar_path)
    hashes = _hash_file(jar_path) if size <= max_jar_bytes else {"sha1": None, "sha512": None}
    if size > max_jar_bytes:
        return _heuristic_asset(
            relative_path=relative_path,
            folder=folder,
            jar_path=jar_path,
            size=size,
            hashes=hashes,
            reason="file_too_large",
        )

    try:
        with zipfile.ZipFile(jar_path) as archive:
            metadata = _read_first_metadata(archive)
    except (OSError, zipfile.BadZipFile):
        return _heuristic_asset(
            relative_path=relative_path,
            folder=folder,
            jar_path=jar_path,
            size=size,
            hashes=hashes,
            reason="bad_jar",
        )

    if metadata is None:
        return _heuristic_asset(
            relative_path=relative_path,
            folder=folder,
            jar_path=jar_path,
            size=size,
            hashes=hashes,
            reason="metadata_missing",
        )

    metadata_name, content = metadata
    parsed = _parse_metadata(metadata_name, content)
    if not parsed:
        return _heuristic_asset(
            relative_path=relative_path,
            folder=folder,
            jar_path=jar_path,
            size=size,
            hashes=hashes,
            reason="metadata_unreadable",
            metadata_source=metadata_name,
        )

    return AddonAsset(
        relative_path=relative_path,
        file_name=jar_path.name,
        folder=folder,
        kind=parsed.get("kind", _kind_from_folder(folder)),
        addon_id=parsed.get("addon_id"),
        name=parsed.get("name"),
        version=parsed.get("version"),
        loader=parsed.get("loader"),
        environment=parsed.get("environment"),
        minecraft_versions=parsed.get("minecraft_versions", []),
        dependencies=parsed.get("dependencies", []),
        conflicts=parsed.get("conflicts", []),
        sha1=hashes["sha1"],
        sha512=hashes["sha512"],
        file_size_bytes=size,
        metadata_source=metadata_name,
        metadata_confidence="metadata",
    )


def diagnose_addons(
    assets: list[AddonAsset],
    core: ServerCore,
    server_dir: Path,
) -> list[AddonDiagnostic]:
    diagnostics: list[AddonDiagnostic] = []
    diagnostics.extend(_diagnose_placement_and_loader(assets, core))
    diagnostics.extend(_diagnose_duplicates(assets))
    diagnostics.extend(_diagnose_dependencies(assets))
    diagnostics.extend(_diagnose_minecraft_versions(assets, core.version))
    diagnostics.extend(_diagnose_config_files(server_dir))
    return diagnostics


def diagnose_runtime_logs(events: list[dict[str, Any]]) -> list[AddonDiagnostic]:
    diagnostics: list[AddonDiagnostic] = []
    seen: set[tuple[str, str]] = set()
    for event in events:
        message = str(event.get("message") or event.get("raw_line") or "")
        raw = str(event.get("raw_line") or message)
        level = str(event.get("level") or "").upper()
        lowered = message.lower()
        category = None
        diagnostic_message = None
        if "failed to load plugin" in lowered or "could not load plugin" in lowered:
            category = "plugin_load_failure"
            diagnostic_message = "启动日志显示插件加载失败。"
        elif "missing" in lowered and ("dependenc" in lowered or "mod" in lowered):
            category = "runtime_missing_dependency"
            diagnostic_message = "启动日志显示存在缺失依赖。"
        elif "mixin" in lowered and ("failed" in lowered or "error" in lowered or "crash" in lowered):
            category = "runtime_mixin_failure"
            diagnostic_message = "启动日志显示 Mixin 应用失败，可能存在 mod 冲突或版本不兼容。"
        elif "classnotfoundexception" in lowered or "nosuchmethoderror" in lowered:
            category = "runtime_binary_incompatibility"
            diagnostic_message = "启动日志显示类或方法缺失，可能存在运行期 API 不兼容。"

        if not category or not diagnostic_message:
            continue
        key = (category, message[:160])
        if key in seen:
            continue
        seen.add(key)
        diagnostics.append(
            AddonDiagnostic(
                severity="BLOCKER" if level in {"ERROR", "FATAL", "SEVERE"} else "HIGH",
                category=category,
                message=diagnostic_message,
                evidence_type="runtime_log",
                confidence="runtime_log",
                affected_files=[],
                evidence={
                    "level": level,
                    "message": message,
                    "raw_line": raw,
                    "event_time": event.get("event_time"),
                },
                suggested_actions=[
                    {
                        "type": "inspect_log_context",
                        "label": "查看该错误附近的启动日志",
                    }
                ],
            )
        )
    return diagnostics


def normalize_severity(severity: str, evidence_type: str) -> str:
    normalized = severity.upper()
    if normalized not in SEVERITIES:
        normalized = "INFO"
    if evidence_type == HEURISTIC_EVIDENCE_TYPE and normalized in {"BLOCKER", "HIGH"}:
        return "MEDIUM"
    if normalized == "BLOCKER" and evidence_type not in HARD_EVIDENCE_TYPES:
        return "HIGH"
    return normalized


def normalize_addon_key(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _read_first_metadata(archive: zipfile.ZipFile) -> tuple[str, str] | None:
    names = set(archive.namelist())
    for metadata_file in _METADATA_FILES:
        if metadata_file not in names:
            continue
        raw = archive.read(metadata_file)
        return metadata_file, raw.decode("utf-8", errors="replace")
    return None


def _parse_metadata(metadata_name: str, content: str) -> dict[str, Any] | None:
    if metadata_name == "fabric.mod.json":
        return _parse_fabric_json(content)
    if metadata_name == "quilt.mod.json":
        return _parse_quilt_json(content)
    if metadata_name in {"META-INF/mods.toml", "META-INF/neoforge.mods.toml"}:
        return _parse_forge_toml(content, loader="neoforge" if "neoforge" in metadata_name else "forge")
    if metadata_name in _PLUGIN_METADATA_FILES:
        return _parse_plugin_yaml(content, loader="bungee" if metadata_name == "bungee.yml" else "bukkit")
    return None


def _parse_fabric_json(content: str) -> dict[str, Any] | None:
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return None
    addon_id = _string(data.get("id"))
    depends = _dependencies_from_mapping(data.get("depends"), source="fabric.mod.json")
    breaks = _dependencies_from_mapping(data.get("breaks"), source="fabric.mod.json")
    conflicts = _dependencies_from_mapping(data.get("conflicts"), source="fabric.mod.json")
    minecraft_versions = [
        dep["version_range"]
        for dep in depends
        if normalize_addon_key(dep.get("addon_id")) == "minecraft" and dep.get("version_range")
    ]
    return {
        "kind": "mod",
        "addon_id": addon_id,
        "name": _string(data.get("name")) or addon_id,
        "version": _string(data.get("version")),
        "loader": "fabric",
        "environment": _normalize_environment(_string(data.get("environment"))),
        "minecraft_versions": minecraft_versions,
        "dependencies": [
            dep for dep in depends if normalize_addon_key(dep.get("addon_id")) not in PSEUDO_DEPENDENCIES
        ],
        "conflicts": breaks + conflicts,
    }


def _parse_quilt_json(content: str) -> dict[str, Any] | None:
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return None
    loader = data.get("quilt_loader") if isinstance(data.get("quilt_loader"), dict) else data
    addon_id = _string(loader.get("id")) if isinstance(loader, dict) else None
    metadata = loader.get("metadata", {}) if isinstance(loader, dict) and isinstance(loader.get("metadata"), dict) else {}
    depends = _dependencies_from_quilt_list(loader.get("depends"), source="quilt.mod.json") if isinstance(loader, dict) else []
    breaks = _dependencies_from_quilt_list(loader.get("breaks"), source="quilt.mod.json") if isinstance(loader, dict) else []
    minecraft_versions = [
        dep["version_range"]
        for dep in depends
        if normalize_addon_key(dep.get("addon_id")) == "minecraft" and dep.get("version_range")
    ]
    return {
        "kind": "mod",
        "addon_id": addon_id,
        "name": _string(metadata.get("name")) or addon_id,
        "version": _string(loader.get("version")) if isinstance(loader, dict) else None,
        "loader": "quilt",
        "environment": _normalize_environment(_string(loader.get("environment"))) if isinstance(loader, dict) else None,
        "minecraft_versions": minecraft_versions,
        "dependencies": [
            dep for dep in depends if normalize_addon_key(dep.get("addon_id")) not in PSEUDO_DEPENDENCIES
        ],
        "conflicts": breaks,
    }


def _parse_forge_toml(content: str, loader: str) -> dict[str, Any] | None:
    try:
        data = tomllib.loads(content)
    except tomllib.TOMLDecodeError:
        return None
    mods = data.get("mods") if isinstance(data.get("mods"), list) else []
    first_mod = next((item for item in mods if isinstance(item, dict)), {})
    addon_id = _string(first_mod.get("modId"))
    dependencies: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    minecraft_versions: list[str] = []
    dependency_blocks = data.get("dependencies", {})
    if isinstance(dependency_blocks, dict):
        blocks = dependency_blocks.values()
    else:
        blocks = []
    for entries in blocks:
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            dep_id = _string(entry.get("modId"))
            if not dep_id:
                continue
            dep = {
                "addon_id": dep_id,
                "required": bool(entry.get("mandatory", False)),
                "version_range": _string(entry.get("versionRange")),
                "side": _normalize_environment(_string(entry.get("side"))),
                "source": "mods.toml",
            }
            dep_key = normalize_addon_key(dep_id)
            dep_type = _string(entry.get("type"))
            if dep_key == "minecraft" and dep.get("version_range"):
                minecraft_versions.append(str(dep["version_range"]))
            elif dep_type and dep_type.lower() in {"incompatible", "breaks", "conflicts"}:
                conflicts.append(dep)
            elif dep_key not in PSEUDO_DEPENDENCIES and dep["required"]:
                dependencies.append(dep)
    return {
        "kind": "mod",
        "addon_id": addon_id,
        "name": _string(first_mod.get("displayName")) or addon_id,
        "version": _string(first_mod.get("version")),
        "loader": loader,
        "environment": _normalize_environment(_string(first_mod.get("side"))),
        "minecraft_versions": minecraft_versions,
        "dependencies": dependencies,
        "conflicts": conflicts,
    }


def _parse_plugin_yaml(content: str, loader: str) -> dict[str, Any] | None:
    parsed = _parse_simple_yaml(content)
    name = _string(parsed.get("name"))
    if not name:
        return None
    hard_depends = _yaml_list(parsed.get("depend")) + _paper_dependencies(parsed)
    soft_depends = _yaml_list(parsed.get("softdepend"))
    dependencies = [
        {
            "addon_id": dep,
            "required": True,
            "version_range": None,
            "side": None,
            "source": "plugin.yml",
        }
        for dep in hard_depends
    ]
    optional = [
        {
            "addon_id": dep,
            "required": False,
            "version_range": None,
            "side": None,
            "source": "plugin.yml",
        }
        for dep in soft_depends
    ]
    return {
        "kind": "plugin",
        "addon_id": name,
        "name": name,
        "version": _string(parsed.get("version")),
        "loader": loader,
        "environment": "server",
        "minecraft_versions": [_string(parsed.get("api-version"))] if parsed.get("api-version") else [],
        "dependencies": dependencies + optional,
        "conflicts": [],
    }


def _parse_simple_yaml(content: str) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    current_list_key: str | None = None
    for raw_line in content.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if indent and current_list_key and stripped.startswith("-"):
            parsed.setdefault(current_list_key, []).append(stripped[1:].strip().strip("\"'"))
            continue
        if indent:
            current_list_key = None
            continue
        key, sep, value = stripped.partition(":")
        if not sep:
            current_list_key = None
            continue
        key = key.strip()
        value = value.strip().strip("\"'")
        current_list_key = None
        if value == "":
            parsed[key] = []
            current_list_key = key
        elif value.startswith("[") and value.endswith("]"):
            parsed[key] = [
                part.strip().strip("\"'")
                for part in value[1:-1].split(",")
                if part.strip()
            ]
        else:
            parsed[key] = value
    return parsed


def _paper_dependencies(parsed: dict[str, Any]) -> list[str]:
    value = parsed.get("dependencies")
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def _dependencies_from_mapping(value: Any, source: str) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    dependencies = []
    for dep_id, dep_value in value.items():
        if dep_value is False:
            continue
        dependencies.append({
            "addon_id": str(dep_id),
            "required": True,
            "version_range": None if dep_value is True else str(dep_value),
            "side": None,
            "source": source,
        })
    return dependencies


def _dependencies_from_quilt_list(value: Any, source: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    dependencies = []
    for item in value:
        if isinstance(item, str):
            dependencies.append({
                "addon_id": item,
                "required": True,
                "version_range": None,
                "side": None,
                "source": source,
            })
        elif isinstance(item, dict):
            dep_id = _string(item.get("id") or item.get("mod") or item.get("modid"))
            if dep_id:
                dependencies.append({
                    "addon_id": dep_id,
                    "required": True,
                    "version_range": _string(item.get("versions") or item.get("version")),
                    "side": _normalize_environment(_string(item.get("environment"))),
                    "source": source,
                })
    return dependencies


def _diagnose_placement_and_loader(
    assets: list[AddonAsset],
    core: ServerCore,
) -> list[AddonDiagnostic]:
    diagnostics: list[AddonDiagnostic] = []
    core_loader = _server_loader(core)
    for asset in assets:
        if asset.kind == "mod" and asset.folder == "plugins":
            diagnostics.append(_placement_diagnostic(asset, "mod_in_plugins", "mod 被放在 plugins/ 中，服务端插件加载器不会按 mod 规则加载它。"))
        if asset.kind == "plugin" and asset.folder == "mods":
            diagnostics.append(_placement_diagnostic(asset, "plugin_in_mods", "插件被放在 mods/ 中，mod 加载器不会按插件规则加载它。"))
        if asset.kind == "mod" and asset.environment == "client":
            diagnostics.append(
                AddonDiagnostic(
                    severity="BLOCKER",
                    category="client_only_mod",
                    message=f"{asset.name or asset.file_name} 标记为客户端 mod，不能放入专用服务端。",
                    evidence_type="metadata",
                    confidence="metadata",
                    affected_files=[asset.relative_path],
                    evidence={"environment": asset.environment, "metadata_source": asset.metadata_source},
                    suggested_actions=[_disable_action(asset), _move_to_quarantine_action(asset)],
                )
            )
        if core_loader and asset.kind == "mod" and asset.loader and not _loader_compatible(asset.loader, core.name):
            diagnostics.append(
                AddonDiagnostic(
                    severity="BLOCKER",
                    category="loader_mismatch",
                    message=f"{asset.name or asset.file_name} 是 {asset.loader} mod，但当前服务端核心是 {core.name}。",
                    evidence_type="metadata",
                    confidence="metadata",
                    affected_files=[asset.relative_path],
                    evidence={"asset_loader": asset.loader, "server_core": core.to_dict()},
                    suggested_actions=[_move_to_quarantine_action(asset)],
                )
            )
        if core_loader and asset.kind == "plugin" and not _plugins_supported(core.name):
            diagnostics.append(
                AddonDiagnostic(
                    severity="BLOCKER",
                    category="loader_mismatch",
                    message=f"{asset.name or asset.file_name} 是插件，但当前服务端核心 {core.name} 不支持 Bukkit/Paper 插件。",
                    evidence_type="metadata",
                    confidence="metadata",
                    affected_files=[asset.relative_path],
                    evidence={"asset_loader": asset.loader, "server_core": core.to_dict()},
                    suggested_actions=[_move_to_quarantine_action(asset)],
                )
            )
    return diagnostics


def _diagnose_duplicates(assets: list[AddonAsset]) -> list[AddonDiagnostic]:
    groups: dict[tuple[str, str], list[AddonAsset]] = {}
    for asset in assets:
        key = normalize_addon_key(asset.addon_id)
        if not key:
            continue
        groups.setdefault((asset.kind, key), []).append(asset)
    diagnostics: list[AddonDiagnostic] = []
    for (_kind, key), group in groups.items():
        if len(group) <= 1:
            continue
        versions = sorted({asset.version or "unknown" for asset in group})
        files = [asset.relative_path for asset in group]
        diagnostics.append(
            AddonDiagnostic(
                severity="BLOCKER",
                category="duplicate_addon",
                message=(
                    f"检测到重复组件 {key}。"
                    if len(versions) == 1
                    else f"检测到同一组件的多个版本：{', '.join(versions)}。"
                ),
                evidence_type="dependency_graph",
                confidence="dependency_graph",
                affected_files=files,
                evidence={"addon_key": key, "versions": versions},
                suggested_actions=[
                    {
                        "type": "keep_one_version",
                        "label": "只保留一个与服务端版本匹配的 jar",
                        "affected_files": files,
                    }
                ],
            )
        )
    return diagnostics


def _diagnose_dependencies(assets: list[AddonAsset]) -> list[AddonDiagnostic]:
    installed = {
        normalize_addon_key(asset.addon_id): asset
        for asset in assets
        if asset.addon_id
    }
    diagnostics: list[AddonDiagnostic] = []
    for asset in assets:
        for dependency in asset.dependencies:
            dep_id = str(dependency.get("addon_id") or "")
            dep_key = normalize_addon_key(dep_id)
            if not dep_key or dep_key in PSEUDO_DEPENDENCIES or not dependency.get("required", True):
                continue
            if dep_key not in installed:
                diagnostics.append(
                    AddonDiagnostic(
                        severity="BLOCKER",
                        category="missing_dependency",
                        message=f"{asset.name or asset.file_name} 缺少必需依赖 {dep_id}。",
                        evidence_type="dependency_graph",
                        confidence="dependency_graph",
                        affected_files=[asset.relative_path],
                        evidence={"dependency": dependency, "installed_addons": sorted(installed)},
                        suggested_actions=[
                            {
                                "type": "install_dependency",
                                "label": f"安装依赖 {dep_id} 的服务端兼容版本",
                            }
                        ],
                    )
                )
        for conflict in asset.conflicts:
            conflict_id = str(conflict.get("addon_id") or "")
            conflict_key = normalize_addon_key(conflict_id)
            if not conflict_key or conflict_key not in installed:
                continue
            other = installed[conflict_key]
            if other.relative_path == asset.relative_path:
                continue
            diagnostics.append(
                AddonDiagnostic(
                    severity="BLOCKER",
                    category="declared_conflict",
                    message=f"{asset.name or asset.file_name} 声明与 {conflict_id} 不兼容。",
                    evidence_type="dependency_graph",
                    confidence="dependency_graph",
                    affected_files=[asset.relative_path, other.relative_path],
                    evidence={"conflict": conflict},
                    suggested_actions=[
                        {
                            "type": "choose_one",
                            "label": "保留其中一个组件，移除或隔离另一个",
                            "affected_files": [asset.relative_path, other.relative_path],
                        }
                    ],
                )
            )
    return diagnostics


def _diagnose_minecraft_versions(
    assets: list[AddonAsset],
    server_version: str | None,
) -> list[AddonDiagnostic]:
    if not server_version:
        return []
    diagnostics: list[AddonDiagnostic] = []
    for asset in assets:
        ranges = [item for item in asset.minecraft_versions if item]
        if not ranges or _version_matches_any(server_version, ranges):
            continue
        diagnostics.append(
            AddonDiagnostic(
                severity="BLOCKER",
                category="minecraft_version_mismatch",
                message=f"{asset.name or asset.file_name} 的 Minecraft 版本范围不包含当前服务端 {server_version}。",
                evidence_type="metadata",
                confidence="metadata",
                affected_files=[asset.relative_path],
                evidence={"server_version": server_version, "declared_ranges": ranges},
                suggested_actions=[
                    {
                        "type": "replace_version",
                        "label": f"替换为支持 Minecraft {server_version} 的版本",
                    }
                ],
            )
        )
    return diagnostics


def _diagnose_config_files(server_dir: Path) -> list[AddonDiagnostic]:
    diagnostics: list[AddonDiagnostic] = []
    server_properties = server_dir / "server.properties"
    if not server_properties.is_file():
        return diagnostics
    try:
        content = server_properties.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return diagnostics
    doc = parse_properties(content)
    for key in ("server-port", "query.port", "rcon.port"):
        count = doc.entry_count(key)
        if count > 1:
            diagnostics.append(
                AddonDiagnostic(
                    severity="MEDIUM",
                    category="duplicate_config_key",
                    message=f"server.properties 中 {key} 出现 {count} 次，可能导致配置认知不一致。",
                    evidence_type="metadata",
                    confidence="metadata",
                    affected_files=["server.properties"],
                    evidence={"key": key, "duplicate_count": count},
                    suggested_actions=[
                        {
                            "type": "edit_config",
                            "label": f"只保留一个 {key} 配置项",
                            "relative_path": "server.properties",
                        }
                    ],
                )
            )
    server_port = doc.get("server-port")
    rcon_port = doc.get("rcon.port")
    if server_port and rcon_port and server_port == rcon_port:
        diagnostics.append(
            AddonDiagnostic(
                severity="HIGH",
                category="port_conflict",
                message="server-port 与 rcon.port 使用了同一个端口，RCON 可能无法正常监听。",
                evidence_type="metadata",
                confidence="metadata",
                affected_files=["server.properties"],
                evidence={"server-port": server_port, "rcon.port": rcon_port},
                suggested_actions=[
                    {
                        "type": "edit_config",
                        "label": "将 rcon.port 改为独立端口",
                        "relative_path": "server.properties",
                    }
                ],
            )
        )
    return diagnostics


def _placement_diagnostic(asset: AddonAsset, category: str, message: str) -> AddonDiagnostic:
    return AddonDiagnostic(
        severity="BLOCKER",
        category=category,
        message=message,
        evidence_type="metadata" if asset.metadata_confidence == "metadata" else HEURISTIC_EVIDENCE_TYPE,
        confidence=asset.metadata_confidence,
        affected_files=[asset.relative_path],
        evidence={
            "kind": asset.kind,
            "folder": asset.folder,
            "metadata_source": asset.metadata_source,
        },
        suggested_actions=[_move_to_quarantine_action(asset)],
    )


def _heuristic_asset(
    *,
    relative_path: str,
    folder: str,
    jar_path: Path,
    size: int,
    hashes: dict[str, str | None],
    reason: str,
    metadata_source: str | None = None,
) -> AddonAsset:
    fallback_name = _strip_version_suffix(jar_path.stem)
    return AddonAsset(
        relative_path=relative_path,
        file_name=jar_path.name,
        folder=folder,
        kind=_kind_from_folder(folder),
        addon_id=fallback_name,
        name=fallback_name,
        version=_version_from_filename(jar_path.stem, fallback_name),
        loader=None,
        environment=None,
        sha1=hashes["sha1"],
        sha512=hashes["sha512"],
        file_size_bytes=size,
        metadata_source=metadata_source,
        metadata_confidence="heuristic",
        knowledge={"local_scan_status": reason},
    )


def _kind_from_folder(folder: str) -> str:
    if folder == "mods":
        return "mod"
    if folder == "plugins":
        return "plugin"
    return "unknown"


def _server_loader(core: ServerCore) -> str | None:
    name = core.name.lower()
    if name in {"fabric", "quilt", "forge", "neoforge"}:
        return name
    if _plugins_supported(core.name):
        return "bukkit"
    if name in {"mohist", "arclight", "catserver"}:
        return "hybrid"
    if name == "unknown":
        return None
    return name


def _loader_compatible(asset_loader: str, core_name: str) -> bool:
    loader = asset_loader.lower()
    core = core_name.lower()
    if core in {"mohist", "arclight", "catserver"}:
        return loader in {"forge", "neoforge"}
    return loader == core


def _plugins_supported(core_name: str) -> bool:
    return core_name in {
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


def _version_matches_any(server_version: str, ranges: list[str]) -> bool:
    return any(_version_matches(server_version, item) for item in ranges)


def _version_matches(server_version: str, range_text: str) -> bool:
    text = str(range_text).strip()
    if not text or text == "*":
        return True
    cleaned = text.strip("[]() ")
    if text == server_version or cleaned == server_version:
        return True
    if "||" in text:
        return any(_version_matches(server_version, part) for part in text.split("||"))
    if "," in cleaned and text[:1] in "[(":
        lower, _, upper = cleaned.partition(",")
        lower_ok = not lower.strip() or _compare_versions(server_version, lower.strip()) >= 0
        upper_ok = not upper.strip() or _compare_versions(server_version, upper.strip()) < 0
        return lower_ok and upper_ok
    tokens = re.split(r"\s+", text)
    if len(tokens) > 1:
        return all(_version_matches(server_version, token) for token in tokens if token)
    match = re.match(r"(>=|<=|>|<|=)?\s*([0-9][0-9A-Za-z_.+-]*)", text)
    if not match:
        return server_version in text
    op = match.group(1) or "="
    target = match.group(2)
    comparison = _compare_versions(server_version, target)
    if op == ">=":
        return comparison >= 0
    if op == "<=":
        return comparison <= 0
    if op == ">":
        return comparison > 0
    if op == "<":
        return comparison < 0
    return comparison == 0


def _compare_versions(left: str, right: str) -> int:
    left_parts = _version_parts(left)
    right_parts = _version_parts(right)
    max_len = max(len(left_parts), len(right_parts))
    left_parts += [0] * (max_len - len(left_parts))
    right_parts += [0] * (max_len - len(right_parts))
    return (left_parts > right_parts) - (left_parts < right_parts)


def _version_parts(value: str) -> list[int]:
    return [int(part) for part in re.findall(r"\d+", value)[:4]]


def _hash_file(path: Path) -> dict[str, str | None]:
    sha1 = hashlib.sha1()
    sha512 = hashlib.sha512()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                sha1.update(chunk)
                sha512.update(chunk)
    except OSError:
        return {"sha1": None, "sha512": None}
    return {"sha1": sha1.hexdigest(), "sha512": sha512.hexdigest()}


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


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


def _string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _yaml_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _normalize_environment(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"*", "both", "universal", "common"}:
        return "both"
    if normalized in {"client", "clientside", "client_only"}:
        return "client"
    if normalized in {"server", "dedicated_server", "serverside", "server_only"}:
        return "server"
    return normalized


def _disable_action(asset: AddonAsset) -> dict[str, Any]:
    return {
        "type": "disable_jar",
        "label": f"禁用 {asset.file_name}",
        "relative_path": asset.relative_path,
        "target_path": f"{asset.relative_path}.disabled",
        "confirmation_required": True,
    }


def _move_to_quarantine_action(asset: AddonAsset) -> dict[str, Any]:
    return {
        "type": "move_to_quarantine",
        "label": f"隔离 {asset.file_name}",
        "relative_path": asset.relative_path,
        "target_path": f"addon_quarantine/{asset.file_name}",
        "confirmation_required": True,
    }
