from __future__ import annotations

import os
import re
import secrets
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil

from src.ai.model_discovery import ModelDiscoveryConfig, list_provider_model_ids
from src.config.settings import PROJECT_ROOT, Settings, load_settings
from src.mc.server_process import discover_start_scripts
from src.service.java_environment_service import JavaEnvironmentService


PROVIDER_DEFAULTS = {
    "deepseek": {
        "id": "deepseek",
        "kind": "builtin",
        "label": "DeepSeek",
        "api_key": "DEEPSEEK_API_KEY",
        "base_url": "DEEPSEEK_BASE_URL",
        "model": "DEEPSEEK_MODEL",
        "default_base_url": "https://api.deepseek.com",
        "default_model": "deepseek-v4-flash",
    },
    "qwen": {
        "id": "qwen",
        "kind": "builtin",
        "label": "Qwen",
        "api_key": "QWEN_API_KEY",
        "base_url": "QWEN_BASE_URL",
        "model": "QWEN_MODEL",
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen3.5-plus",
    },
}
_ENV_ASSIGNMENT_RE = re.compile(r"^(?P<prefix>\s*(?:export\s+)?)(?P<key>[A-Z][A-Z0-9_]*)=")
_PLACEHOLDER_VALUES = {"replace_me", "changeme", "your_api_key"}


@dataclass(frozen=True)
class EnvironmentBootstrapResult:
    env_path: Path
    created: bool


class EnvironmentSettingsError(RuntimeError):
    pass


class EnvironmentSettingsService:
    def __init__(
        self,
        *,
        project_root: Path = PROJECT_ROOT,
        env_path: Path | None = None,
        environ: Mapping[str, str] | None = None,
        java_environment_service: JavaEnvironmentService | None = None,
        model_lister: Callable[[ModelDiscoveryConfig], list[str]] | None = None,
        memory_total_bytes: Callable[[], int] | None = None,
    ) -> None:
        self._project_root = project_root
        self._env_path = env_path or project_root / ".env"
        self._environ = os.environ if environ is None else environ
        self._java_environment = java_environment_service
        self._model_lister = model_lister or list_provider_model_ids
        self._memory_total_bytes = memory_total_bytes or (
            lambda: int(psutil.virtual_memory().total)
        )

    @classmethod
    def ensure_environment_file(
        cls,
        *,
        project_root: Path = PROJECT_ROOT,
        env_path: Path | None = None,
        example_path: Path | None = None,
        memory_total_bytes: Callable[[], int] | None = None,
    ) -> EnvironmentBootstrapResult:
        target = env_path or project_root / ".env"
        if target.exists():
            return EnvironmentBootstrapResult(env_path=target, created=False)

        source = example_path or project_root / ".env.example"
        if not source.is_file():
            raise EnvironmentSettingsError(f"缺少环境配置模板：{source}")
        try:
            content = source.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise EnvironmentSettingsError(f"无法读取环境配置模板：{exc}") from exc

        total_bytes = (
            memory_total_bytes() if memory_total_bytes is not None
            else int(psutil.virtual_memory().total)
        )
        updates = {
            "DEEPSEEK_API_KEY": "",
            "QWEN_API_KEY": "",
            "MC_RCON_PASSWORD": secrets.token_urlsafe(24),
            "MC_JAVA_XMX": recommend_java_xmx(total_bytes),
        }
        rendered = _update_env_text(content, updates)
        _atomic_write_text(target, rendered)
        return EnvironmentBootstrapResult(env_path=target, created=True)

    def inspect(self) -> dict[str, Any]:
        settings = load_settings(
            env_file=self._env_path,
            environ=self._environ,
            project_root=self._project_root,
        )
        provider = _normalized_provider(
            settings.ai_default_provider,
            settings=settings,
        )
        provider_view = (
            self._provider_view(settings, provider)
            or _empty_provider_view(provider)
        )
        server_detection = self._detect_server(settings)
        java_detection = self._detect_java()
        current_java_path = settings.mc_java_path
        selected_java = java_detection.get("selected_java") or {}
        if selected_java.get("java_path"):
            current_java_path = str(selected_java["java_path"])

        memory_recommendation = recommend_java_xmx(self._memory_total_bytes())
        locked = sorted(
            key
            for key in _managed_env_keys()
            if key in self._environ
        )
        return {
            "provider": provider,
            "providers": self._visible_provider_views(settings),
            "api_key_configured": bool(provider_view.get("api_key_configured")),
            "base_url": str(provider_view.get("base_url") or ""),
            "server_dir": str(server_detection["server_dir"]),
            "server_detection": server_detection,
            "java_xmx": settings.mc_java_xmx or memory_recommendation,
            "recommended_java_xmx": memory_recommendation,
            "server_jar": str(server_detection.get("server_jar") or settings.mc_server_jar.name),
            "java_path": current_java_path,
            "java_detection": java_detection,
            "rcon_configured": _is_secret_configured(settings.mc_rcon_password),
            "locked_fields": locked,
            "env_path": str(self._env_path),
        }

    def test_ai_connection(
        self,
        provider: str,
        api_key: str,
        base_url: str,
    ) -> dict[str, Any]:
        settings = load_settings(
            env_file=self._env_path,
            environ=self._environ,
            project_root=self._project_root,
        )
        normalized = _normalized_provider(provider, settings=settings)
        provider_view = self._provider_view(settings, normalized)
        if provider_view is None:
            provider_view = _empty_provider_view(normalized)
        effective_key = api_key.strip() or str(provider_view.get("api_key") or "")
        effective_url = (
            base_url.strip()
            or str(provider_view.get("base_url") or "")
            or str(provider_view.get("default_base_url") or "")
        )
        if not _is_secret_configured(effective_key):
            return {
                "status": "failed",
                "provider": normalized,
                "model_count": 0,
                "message": "请先填写 API Key。",
            }
        if not effective_url.startswith(("http://", "https://")):
            return {
                "status": "failed",
                "provider": normalized,
                "model_count": 0,
                "message": "Base URL 必须以 http:// 或 https:// 开头。",
            }
        try:
            models = self._model_lister(
                ModelDiscoveryConfig(
                    provider=normalized,
                    api_key=effective_key,
                    base_url=effective_url,
                    timeout_seconds=settings.ai_model_discovery_timeout_seconds,
                )
            )
        except Exception as exc:
            return {
                "status": "failed",
                "provider": normalized,
                "model_count": 0,
                "message": f"连接失败：{exc}",
            }
        return {
            "status": "ok",
            "provider": normalized,
            "model_count": len(models),
            "models": models,
            "message": f"连接成功，发现 {len(models)} 个可用模型。",
        }

    def save_basic_settings(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        settings = load_settings(
            env_file=self._env_path,
            environ=self._environ,
            project_root=self._project_root,
        )
        current_provider = _normalized_provider(
            settings.ai_default_provider,
            settings=settings,
        )
        provider_payloads = list(payload.get("providers") or [])
        if provider_payloads:
            updates, default_provider = self._provider_updates_from_payloads(
                provider_payloads,
                preferred_default=str(payload.get("provider") or current_provider),
            )
        else:
            default_provider = _normalized_provider(str(payload.get("provider") or ""))
            provider_config = PROVIDER_DEFAULTS[default_provider]
            updates = {
                "AI_DEFAULT_PROVIDER": default_provider,
            }
            base_url = str(payload.get("base_url") or "").strip()
            if not base_url:
                base_url = provider_config["default_base_url"]
            if not base_url.startswith(("http://", "https://")):
                raise EnvironmentSettingsError("Base URL 必须以 http:// 或 https:// 开头。")
            updates[provider_config["base_url"]] = base_url
            api_key = str(payload.get("api_key") or "").strip()
            clear_api_key = bool(payload.get("clear_api_key"))
            if clear_api_key:
                updates[provider_config["api_key"]] = ""
            elif api_key:
                updates[provider_config["api_key"]] = api_key

        server_dir_value = str(
            payload.get("server_dir") or settings.mc_server_dir
        ).strip()
        server_dir = _resolve_path(server_dir_value, self._project_root)
        updates["MC_SERVER_DIR"] = _portable_path(server_dir, self._project_root)
        updates["MC_LOG_PATH"] = _portable_path(
            server_dir / "logs/latest.log",
            self._project_root,
        )

        java_xmx = str(payload.get("java_xmx") or "").strip().upper()
        if not re.fullmatch(r"[1-9][0-9]*[MG]", java_xmx):
            raise EnvironmentSettingsError("最大内存请使用 2G、4096M 这样的格式。")
        updates["MC_JAVA_XMX"] = java_xmx

        server_jar = str(payload.get("server_jar") or "").strip()
        if server_jar:
            updates["MC_SERVER_JAR"] = server_jar
        java_path = str(payload.get("java_path") or "").strip()
        if java_path:
            updates["MC_JAVA_PATH"] = java_path

        if not _is_secret_configured(settings.mc_rcon_password):
            updates["MC_RCON_PASSWORD"] = secrets.token_urlsafe(24)

        locked_fields = {
            key for key in updates
            if key in self._environ
        }
        file_updates = {
            key: value for key, value in updates.items()
            if key not in locked_fields
        }
        try:
            content = self._env_path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise EnvironmentSettingsError("未找到 .env，请重新启动应用。") from exc
        except (OSError, UnicodeError) as exc:
            raise EnvironmentSettingsError(f"无法读取 .env：{exc}") from exc

        _atomic_write_text(self._env_path, _update_env_text(content, file_updates))
        return {
            "status": "saved",
            "provider": default_provider,
            "env_path": str(self._env_path),
            "locked_fields": sorted(locked_fields),
            "message": (
                "设置已保存。"
                if not locked_fields
                else "设置已保存；部分字段由系统环境变量控制，未修改其运行时值。"
            ),
        }

    def _detect_server(self, settings: Settings) -> dict[str, Any]:
        configured = settings.mc_server_dir.resolve(strict=False)
        candidates = _server_directory_candidates(self._project_root, configured)
        if _is_server_directory(configured):
            selected = configured
            source = "configured"
        elif len(candidates) == 1:
            selected = candidates[0]
            source = "detected"
        else:
            selected = configured
            source = "configured_default"

        scripts = [path.name for path in discover_start_scripts(selected)]
        jars = sorted(selected.glob("*.jar")) if selected.is_dir() else []
        configured_jar = settings.mc_server_jar
        selected_jar: Path | None = None
        if configured_jar.is_file():
            selected_jar = configured_jar
        elif len(jars) == 1:
            selected_jar = jars[0]
        return {
            "server_dir": selected,
            "source": source,
            "candidate_count": len(candidates),
            "candidates": [str(path) for path in candidates],
            "start_scripts": scripts,
            "server_jar": selected_jar.name if selected_jar else settings.mc_server_jar.name,
            "jar_detected": selected_jar is not None,
            "ready": bool(scripts or selected_jar),
        }

    def _detect_java(self) -> dict[str, Any]:
        if self._java_environment is None:
            return {
                "status": "unavailable",
                "selected_java": None,
                "message": "Java 检测将在工作台加载后可用。",
            }
        try:
            return self._java_environment.check_environment()
        except Exception as exc:
            return {
                "status": "failed",
                "selected_java": None,
                "message": f"Java 检测失败：{exc}",
            }

    def _visible_provider_views(self, settings: Settings) -> list[dict[str, Any]]:
        views = [
            view for view in self._all_provider_views(settings)
            if _is_secret_configured(str(view.get("api_key") or ""))
        ]
        if views:
            return views
        default_view = self._provider_view(settings, settings.ai_default_provider)
        return [default_view or _empty_provider_view("deepseek")]

    def _all_provider_views(self, settings: Settings) -> list[dict[str, Any]]:
        views = [
            self._builtin_provider_view(settings, "deepseek"),
            self._builtin_provider_view(settings, "qwen"),
        ]
        for provider in settings.ai_custom_providers:
            views.append({
                "id": provider.id,
                "kind": "custom",
                "label": provider.label,
                "name": provider.label,
                "api_key": provider.api_key,
                "base_url": provider.base_url,
                "fallback_model": provider.fallback_model,
                "default_base_url": "",
                "default_model": "",
                "api_key_configured": _is_secret_configured(provider.api_key),
                "api_key_env_name": provider.api_key_env_name,
                "base_url_env_name": provider.base_url_env_name,
                "model_env_name": provider.model_env_name,
                "name_env_name": provider.api_key_env_name.replace("_API_KEY", "_NAME"),
                "openai_compatible_required": True,
            })
        return views

    def _builtin_provider_view(self, settings: Settings, provider_id: str) -> dict[str, Any]:
        config = PROVIDER_DEFAULTS[provider_id]
        api_key = getattr(settings, f"{provider_id}_api_key")
        base_url = getattr(settings, f"{provider_id}_base_url")
        model = getattr(settings, f"{provider_id}_model")
        return {
            "id": provider_id,
            "kind": "builtin",
            "label": config["label"],
            "name": config["label"],
            "api_key": api_key,
            "base_url": base_url or config["default_base_url"],
            "fallback_model": model or config["default_model"],
            "default_base_url": config["default_base_url"],
            "default_model": config["default_model"],
            "api_key_configured": _is_secret_configured(api_key),
            "api_key_env_name": config["api_key"],
            "base_url_env_name": config["base_url"],
            "model_env_name": config["model"],
            "name_env_name": "",
            "openai_compatible_required": True,
        }

    def _provider_view(
        self,
        settings: Settings,
        provider_id: str,
    ) -> dict[str, Any] | None:
        normalized = provider_id.strip().lower()
        for view in self._all_provider_views(settings):
            if view["id"] == normalized:
                return view
        return None

    def _provider_updates_from_payloads(
        self,
        provider_payloads: list[Any],
        *,
        preferred_default: str,
    ) -> tuple[dict[str, str], str]:
        updates: dict[str, str] = {}
        used_custom_slots = {
            _custom_slot_from_provider_id(str(item.get("id", "")))
            for item in provider_payloads
            if isinstance(item, Mapping)
        }
        next_custom_slot = _next_custom_slot(used_custom_slots)
        saved_provider_ids: list[str] = []
        configured_provider_ids: list[str] = []
        for item in provider_payloads:
            if not isinstance(item, Mapping):
                continue
            provider_id = str(item.get("id") or "").strip().lower()
            kind = str(item.get("kind") or "").strip().lower()
            if provider_id in PROVIDER_DEFAULTS and kind != "custom":
                env_names = PROVIDER_DEFAULTS[provider_id]
                label = env_names["label"]
            else:
                slot = _custom_slot_from_provider_id(provider_id) or next_custom_slot
                if slot == next_custom_slot:
                    next_custom_slot += 1
                provider_id = f"custom_{slot}"
                env_names = {
                    "api_key": f"AI_CUSTOM_PROVIDER_{slot}_API_KEY",
                    "base_url": f"AI_CUSTOM_PROVIDER_{slot}_BASE_URL",
                    "model": f"AI_CUSTOM_PROVIDER_{slot}_MODEL",
                    "name": f"AI_CUSTOM_PROVIDER_{slot}_NAME",
                }
                label = str(item.get("name") or "").strip() or f"其它服务商 {slot}"
                updates[env_names["name"]] = label

            base_url = str(item.get("base_url") or "").strip()
            if not base_url and provider_id in PROVIDER_DEFAULTS:
                base_url = str(PROVIDER_DEFAULTS[provider_id]["default_base_url"])
            if base_url and not base_url.startswith(("http://", "https://")):
                raise EnvironmentSettingsError(f"{label} 的 Base URL 必须以 http:// 或 https:// 开头。")
            updates[env_names["base_url"]] = base_url

            api_key = str(item.get("api_key") or "").strip()
            if bool(item.get("clear_api_key")):
                updates[env_names["api_key"]] = ""
            else:
                updates[env_names["api_key"]] = api_key
            if _is_secret_configured(api_key):
                configured_provider_ids.append(provider_id)

            fallback_model = str(item.get("fallback_model") or "").strip()
            if fallback_model or provider_id not in PROVIDER_DEFAULTS:
                updates[env_names["model"]] = fallback_model
            saved_provider_ids.append(provider_id)

        if preferred_default in configured_provider_ids:
            default_provider = preferred_default
        elif configured_provider_ids:
            default_provider = configured_provider_ids[0]
        elif preferred_default in saved_provider_ids:
            default_provider = preferred_default
        elif saved_provider_ids:
            default_provider = saved_provider_ids[0]
        else:
            default_provider = "deepseek"
        updates["AI_DEFAULT_PROVIDER"] = default_provider
        return updates, default_provider


def recommend_java_xmx(total_bytes: int) -> str:
    gib = max(0.0, float(total_bytes) / (1024 ** 3))
    if gib < 4:
        return "1G"
    recommended = max(2, min(8, int(gib // 2)))
    return f"{recommended}G"


def _server_directory_candidates(project_root: Path, configured: Path) -> list[Path]:
    candidates: list[Path] = []
    for candidate in (
        configured,
        project_root / "mc_server",
        project_root / "mcServer",
    ):
        resolved = candidate.resolve(strict=False)
        if resolved not in candidates and _is_server_directory(resolved):
            candidates.append(resolved)
    return candidates


def _is_server_directory(path: Path) -> bool:
    if not path.is_dir():
        return False
    return (
        (path / "server.properties").is_file()
        or bool(discover_start_scripts(path))
        or any(path.glob("*.jar"))
    )


def _empty_provider_view(provider_id: str) -> dict[str, Any]:
    if provider_id in PROVIDER_DEFAULTS:
        config = PROVIDER_DEFAULTS[provider_id]
        return {
            "id": provider_id,
            "kind": "builtin",
            "label": config["label"],
            "name": config["label"],
            "api_key": "",
            "base_url": config["default_base_url"],
            "fallback_model": config["default_model"],
            "default_base_url": config["default_base_url"],
            "default_model": config["default_model"],
            "api_key_configured": False,
            "api_key_env_name": config["api_key"],
            "base_url_env_name": config["base_url"],
            "model_env_name": config["model"],
            "name_env_name": "",
            "openai_compatible_required": True,
        }
    slot = _custom_slot_from_provider_id(provider_id) or 1
    return {
        "id": f"custom_{slot}",
        "kind": "custom",
        "label": f"其它服务商 {slot}",
        "name": f"其它服务商 {slot}",
        "api_key": "",
        "base_url": "",
        "fallback_model": "",
        "default_base_url": "",
        "default_model": "",
        "api_key_configured": False,
        "api_key_env_name": f"AI_CUSTOM_PROVIDER_{slot}_API_KEY",
        "base_url_env_name": f"AI_CUSTOM_PROVIDER_{slot}_BASE_URL",
        "model_env_name": f"AI_CUSTOM_PROVIDER_{slot}_MODEL",
        "name_env_name": f"AI_CUSTOM_PROVIDER_{slot}_NAME",
        "openai_compatible_required": True,
    }


def _normalized_provider(value: str, settings: Settings | None = None) -> str:
    normalized = value.strip().lower()
    if normalized not in PROVIDER_DEFAULTS:
        custom_ids = {
            provider.id for provider in getattr(settings, "ai_custom_providers", ())
        } if settings is not None else set()
        if normalized not in custom_ids and _custom_slot_from_provider_id(normalized) is None:
            raise EnvironmentSettingsError(f"不支持的 AI 服务商：{value or '(空)'}")
    return normalized


def _managed_env_keys() -> set[str]:
    keys = {
        "AI_DEFAULT_PROVIDER",
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_BASE_URL",
        "QWEN_API_KEY",
        "QWEN_BASE_URL",
        "DEEPSEEK_MODEL",
        "QWEN_MODEL",
        "MC_SERVER_DIR",
        "MC_SERVER_JAR",
        "MC_JAVA_PATH",
        "MC_JAVA_XMX",
        "MC_LOG_PATH",
        "MC_RCON_PASSWORD",
    }
    for index in range(1, 6):
        keys.update({
            f"AI_CUSTOM_PROVIDER_{index}_NAME",
            f"AI_CUSTOM_PROVIDER_{index}_API_KEY",
            f"AI_CUSTOM_PROVIDER_{index}_BASE_URL",
            f"AI_CUSTOM_PROVIDER_{index}_MODEL",
        })
    return keys


def _custom_slot_from_provider_id(provider_id: str) -> int | None:
    match = re.fullmatch(r"custom_([1-9][0-9]*)", provider_id.strip().lower())
    return int(match.group(1)) if match else None


def _next_custom_slot(used_slots: set[int | None]) -> int:
    slot = 1
    used = {item for item in used_slots if item is not None}
    while slot in used:
        slot += 1
    return slot


def _is_secret_configured(value: str) -> bool:
    normalized = (value or "").strip()
    return bool(normalized and normalized.lower() not in _PLACEHOLDER_VALUES)


def _resolve_path(value: str, project_root: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve(strict=False)


def _portable_path(path: Path, project_root: Path) -> str:
    try:
        return path.relative_to(project_root.resolve(strict=False)).as_posix()
    except ValueError:
        return str(path)


def _update_env_text(content: str, updates: Mapping[str, str]) -> str:
    lines = content.splitlines(keepends=True)
    seen: set[str] = set()
    rendered: list[str] = []
    newline = "\r\n" if "\r\n" in content else "\n"
    for line in lines:
        match = _ENV_ASSIGNMENT_RE.match(line)
        if not match or match.group("key") not in updates:
            rendered.append(line)
            continue
        key = match.group("key")
        suffix = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
        rendered.append(
            f"{match.group('prefix')}{key}={_encode_env_value(updates[key])}{suffix}"
        )
        seen.add(key)
    missing = [key for key in updates if key not in seen]
    if missing:
        if rendered and not rendered[-1].endswith(("\n", "\r\n")):
            rendered[-1] += newline
        if rendered and rendered[-1].strip():
            rendered.append(newline)
        rendered.extend(
            f"{key}={_encode_env_value(updates[key])}{newline}"
            for key in missing
        )
    return "".join(rendered)


def _encode_env_value(value: str) -> str:
    if not value:
        return ""
    if re.fullmatch(r"[A-Za-z0-9_./:@+-]+", value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        os.replace(temp_path, path)
    except OSError as exc:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise EnvironmentSettingsError(f"无法保存 {path.name}：{exc}") from exc
