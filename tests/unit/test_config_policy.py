from __future__ import annotations

from src.mc.config_files import get_config_spec
from src.mc.config_policy import BLOCKED, HIGH, validate_config_value


def _key_spec(key: str) -> dict:
    spec = get_config_spec("server.properties")
    assert spec is not None
    return spec["keys"][key]


def test_config_policy_normalizes_boolean_aliases() -> None:
    result = validate_config_value("pvp", "关闭", _key_spec("pvp"))

    assert result.valid is True
    assert result.normalized_value == "false"


def test_config_policy_rejects_out_of_range_integer() -> None:
    result = validate_config_value("max-players", "999", _key_spec("max-players"))

    assert result.valid is False
    assert result.risk_level == BLOCKED
    assert "不能大于" in (result.error_message or "")


def test_config_policy_normalizes_enum_aliases() -> None:
    result = validate_config_value("difficulty", "普通", _key_spec("difficulty"))

    assert result.valid is True
    assert result.normalized_value == "normal"


def test_config_policy_marks_online_mode_false_high_risk() -> None:
    result = validate_config_value("online-mode", "false", _key_spec("online-mode"))

    assert result.valid is True
    assert result.risk_level == HIGH
    assert result.confirmation_required is True
