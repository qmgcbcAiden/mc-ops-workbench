from __future__ import annotations

from copy import deepcopy
from typing import Any


SERVER_PROPERTIES_PATH = "server.properties"


SERVER_PROPERTIES_SPEC: dict[str, Any] = {
    "relative_path": SERVER_PROPERTIES_PATH,
    "format": "properties",
    "description": "Minecraft 原版服务端核心配置。",
    "keys": {
        "max-players": {
            "type": "integer",
            "min": 1,
            "max": 200,
            "aliases": ["最大人数", "玩家上限", "最多玩家", "max players"],
            "risk": "LOW",
            "restart_required": True,
        },
        "pvp": {
            "type": "boolean",
            "aliases": ["PVP", "玩家互殴", "玩家伤害", "互打"],
            "risk": "LOW",
            "restart_required": True,
        },
        "difficulty": {
            "type": "enum",
            "values": ["peaceful", "easy", "normal", "hard"],
            "aliases": ["难度"],
            "value_aliases": {
                "和平": "peaceful",
                "简单": "easy",
                "普通": "normal",
                "困难": "hard",
            },
            "risk": "MEDIUM",
            "restart_required": True,
        },
        "gamemode": {
            "type": "enum",
            "values": ["survival", "creative", "adventure", "spectator"],
            "aliases": ["默认模式", "游戏模式"],
            "value_aliases": {
                "生存": "survival",
                "创造": "creative",
                "冒险": "adventure",
                "旁观": "spectator",
            },
            "risk": "MEDIUM",
            "restart_required": True,
        },
        "online-mode": {
            "type": "boolean",
            "aliases": ["正版验证", "离线模式", "盗版登录"],
            "risk": "HIGH",
            "restart_required": True,
            "warning": "关闭正版验证会显著降低账号安全性。",
        },
        "enable-command-block": {
            "type": "boolean",
            "aliases": ["命令方块", "command block"],
            "risk": "HIGH",
            "restart_required": True,
            "warning": "开启命令方块会允许地图或管理员运行更强的游戏内命令。",
        },
        "view-distance": {
            "type": "integer",
            "min": 2,
            "max": 32,
            "aliases": ["视距", "区块视野", "视野距离"],
            "risk": "MEDIUM",
            "restart_required": True,
        },
        "simulation-distance": {
            "type": "integer",
            "min": 2,
            "max": 32,
            "aliases": ["模拟距离"],
            "risk": "MEDIUM",
            "restart_required": True,
        },
        "server-port": {
            "type": "integer",
            "min": 1024,
            "max": 65535,
            "aliases": ["端口", "服务器端口"],
            "risk": "HIGH",
            "restart_required": True,
            "warning": "修改服务器端口会影响玩家连接地址。",
        },
        "motd": {
            "type": "string",
            "max_length": 120,
            "aliases": ["服务器描述", "列表标题", "motd"],
            "risk": "LOW",
            "restart_required": True,
        },
        "allow-flight": {
            "type": "boolean",
            "aliases": ["允许飞行", "飞行"],
            "risk": "MEDIUM",
            "restart_required": True,
        },
        "spawn-protection": {
            "type": "integer",
            "min": 0,
            "max": 64,
            "aliases": ["出生点保护", "出生保护"],
            "risk": "MEDIUM",
            "restart_required": True,
        },
        "white-list": {
            "type": "boolean",
            "aliases": ["白名单开关", "白名单"],
            "risk": "HIGH",
            "restart_required": True,
            "warning": "修改白名单开关会影响玩家能否进入服务器。",
        },
    },
}


CONFIG_FILE_SPECS: dict[str, dict[str, Any]] = {
    SERVER_PROPERTIES_PATH: SERVER_PROPERTIES_SPEC,
}


def list_config_files() -> list[dict[str, Any]]:
    return [public_config_spec(spec) for spec in CONFIG_FILE_SPECS.values()]


def get_config_spec(relative_path: str) -> dict[str, Any] | None:
    spec = CONFIG_FILE_SPECS.get(_normalize_relative_path(relative_path))
    return deepcopy(spec) if spec else None


def public_config_spec(spec: dict[str, Any]) -> dict[str, Any]:
    public = deepcopy(spec)
    for key_spec in public.get("keys", {}).values():
        key_spec.pop("value_aliases", None)
    return public


def is_config_file_allowed(relative_path: str) -> bool:
    return _normalize_relative_path(relative_path) in CONFIG_FILE_SPECS


def normalize_config_key(relative_path: str, key_or_alias: str) -> str | None:
    spec = get_config_spec(relative_path)
    if not spec:
        return None

    needle = key_or_alias.strip().lower()
    keys = spec.get("keys", {})
    for key, key_spec in keys.items():
        if needle == key.lower():
            return key
        aliases = [str(alias).lower() for alias in key_spec.get("aliases", [])]
        if needle in aliases:
            return key
    return None


def _normalize_relative_path(relative_path: str) -> str:
    return relative_path.strip().replace("\\", "/").lstrip("/")
