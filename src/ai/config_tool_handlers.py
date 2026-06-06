from __future__ import annotations

import json
from typing import Any

from src.service.config_edit_service import ConfigEditService


class ConfigToolHandlers:
    def __init__(
        self,
        config_service: ConfigEditService,
        allow_auto_apply: bool = True,
    ) -> None:
        self._service = config_service
        self._allow_auto_apply = allow_auto_apply

    def list_config_capabilities(self, _args: dict[str, Any]) -> str:
        return _json(self._service.list_capabilities())

    def read_config_file(self, args: dict[str, Any]) -> str:
        result = self._service.read_config_file(
            relative_path=_string_arg(args, "file"),
        )
        return _json(result)

    def get_config_values(self, args: dict[str, Any]) -> str:
        result = self._service.get_values(
            relative_path=_string_arg(args, "file"),
            keys=_string_list_arg(args, "keys"),
        )
        return _json(result)

    def propose_config_change(self, args: dict[str, Any]) -> str:
        result = self._service.propose_change(
            relative_path=_string_arg(args, "file"),
            changes=_change_list_arg(args, "changes"),
            user_request=_string_arg(args, "user_request"),
            session_id=_optional_string_arg(args, "session_id"),
            allow_auto_apply=self._allow_auto_apply,
        )
        return _json(result)


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False)


def _string_arg(args: dict[str, Any], name: str) -> str:
    value = args.get(name, "")
    return value if isinstance(value, str) else str(value)


def _optional_string_arg(args: dict[str, Any], name: str) -> str | None:
    value = args.get(name)
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)


def _string_list_arg(args: dict[str, Any], name: str) -> list[str]:
    value = args.get(name, [])
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _change_list_arg(args: dict[str, Any], name: str) -> list[dict[str, Any]]:
    value = args.get(name, [])
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]
