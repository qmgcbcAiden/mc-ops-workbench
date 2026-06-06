from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


LOW = "LOW"
MEDIUM = "MEDIUM"
HIGH = "HIGH"
BLOCKED = "BLOCKED"

RISK_ORDER = {
    LOW: 0,
    MEDIUM: 1,
    HIGH: 2,
    BLOCKED: 3,
}

TRUE_VALUES = {"true", "1", "yes", "on", "开启", "打开", "允许", "启用", "是"}
FALSE_VALUES = {"false", "0", "no", "off", "关闭", "禁止", "不允许", "禁用", "否"}


@dataclass(frozen=True)
class ConfigValueResult:
    valid: bool
    normalized_value: str | None
    risk_level: str
    restart_required: bool
    warnings: list[str] = field(default_factory=list)
    error_message: str | None = None

    @property
    def confirmation_required(self) -> bool:
        return self.risk_level == HIGH


def max_risk(*risk_levels: str) -> str:
    result = LOW
    for risk in risk_levels:
        if RISK_ORDER.get(risk, 0) > RISK_ORDER[result]:
            result = risk
    return result


def validate_config_value(key: str, raw_value: object, key_spec: dict[str, Any]) -> ConfigValueResult:
    risk_level = str(key_spec.get("risk", LOW))
    restart_required = bool(key_spec.get("restart_required", False))
    warnings = []
    if key_spec.get("warning"):
        warnings.append(str(key_spec["warning"]))

    text_value = str(raw_value).strip()
    if "\n" in text_value or "\r" in text_value or "\x00" in text_value:
        return _blocked("配置值不能包含换行或二进制空字符。")

    value_type = key_spec.get("type")
    if value_type == "boolean":
        normalized = _normalize_boolean(text_value, key_spec)
        if normalized is None:
            return _blocked(f"{key} 需要布尔值 true/false。")
        return ConfigValueResult(
            valid=True,
            normalized_value=normalized,
            risk_level=_value_sensitive_risk(key, normalized, risk_level),
            restart_required=restart_required,
            warnings=warnings,
        )

    if value_type == "integer":
        try:
            number = int(text_value)
        except ValueError:
            return _blocked(f"{key} 需要整数值。")
        min_value = key_spec.get("min")
        max_value = key_spec.get("max")
        if min_value is not None and number < int(min_value):
            return _blocked(f"{key} 不能小于 {min_value}。")
        if max_value is not None and number > int(max_value):
            return _blocked(f"{key} 不能大于 {max_value}。")
        return ConfigValueResult(
            valid=True,
            normalized_value=str(number),
            risk_level=risk_level,
            restart_required=restart_required,
            warnings=warnings,
        )

    if value_type == "enum":
        normalized = _normalize_enum(text_value, key_spec)
        if normalized is None:
            values = ", ".join(key_spec.get("values", []))
            return _blocked(f"{key} 只允许这些值：{values}。")
        return ConfigValueResult(
            valid=True,
            normalized_value=normalized,
            risk_level=risk_level,
            restart_required=restart_required,
            warnings=warnings,
        )

    if value_type == "string":
        max_length = int(key_spec.get("max_length", 255))
        if len(text_value) > max_length:
            return _blocked(f"{key} 不能超过 {max_length} 个字符。")
        if any(token in text_value.lower() for token in ("&&", "||", "|", ">", "<", "../", "..\\")):
            return _blocked("配置值包含疑似 shell 或路径操作片段。")
        return ConfigValueResult(
            valid=True,
            normalized_value=text_value,
            risk_level=risk_level,
            restart_required=restart_required,
            warnings=warnings,
        )

    return _blocked(f"{key} 的配置类型不受支持。")


def _normalize_boolean(value: str, key_spec: dict[str, Any]) -> str | None:
    lowered = value.lower()
    aliases = {str(k).lower(): str(v) for k, v in key_spec.get("value_aliases", {}).items()}
    if lowered in aliases:
        return aliases[lowered].lower()
    if lowered in TRUE_VALUES:
        return "true"
    if lowered in FALSE_VALUES:
        return "false"
    return None


def _normalize_enum(value: str, key_spec: dict[str, Any]) -> str | None:
    lowered = value.lower()
    aliases = {str(k).lower(): str(v) for k, v in key_spec.get("value_aliases", {}).items()}
    if lowered in aliases:
        return aliases[lowered]

    values = {str(item).lower(): str(item) for item in key_spec.get("values", [])}
    return values.get(lowered)


def _value_sensitive_risk(key: str, value: str, default_risk: str) -> str:
    if key == "online-mode" and value == "false":
        return HIGH
    if key == "enable-command-block" and value == "true":
        return HIGH
    return default_risk


def _blocked(message: str) -> ConfigValueResult:
    return ConfigValueResult(
        valid=False,
        normalized_value=None,
        risk_level=BLOCKED,
        restart_required=False,
        warnings=[],
        error_message=message,
    )
