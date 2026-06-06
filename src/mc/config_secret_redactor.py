from __future__ import annotations

import json
import re
from pathlib import Path

REDACTED_VALUE = "<redacted>"

_EXPLICIT_SECRET_KEYS: set[str] = {
    "rcon.password",
}

_SECRET_KEY_PATTERNS: list[str] = [
    "password",
    "token",
    "secret",
    "api_key",
    "api-key",
    "apikey",
    "credential",
    "private_key",
    "private-key",
]
_ASSIGNMENT_RE = re.compile(
    r"^(?P<indent>\s*)(?P<quote>[\"']?)(?P<key>[^\"':=]+)(?P=quote)"
    r"(?P<separator>\s*[:=]\s*)(?P<value>.*?)(?P<comma>,?\s*)$"
)


def redact_properties(content: str) -> str:
    lines = content.split("\n")
    redacted: list[str] = []
    for line in lines:
        redacted.append(_redact_property_line(line))
    return "\n".join(redacted)


def redact_config_text(relative_path: str, content: str) -> str:
    ext = file_extension(relative_path)
    if ext in {".properties", ".cfg", ".conf", ".ini"} or Path(relative_path).name.lower() == "eula.txt":
        return _redact_assignment_lines(content)
    if ext in {".json", ".mcmeta"}:
        return _redact_json(content)
    if ext in {".json5", ".yaml", ".yml", ".toml"}:
        return _redact_assignment_lines(content)
    return content


def _redact_property_line(line: str) -> str:
    stripped = line.lstrip()
    if stripped.startswith("#") or stripped.startswith("!"):
        return line
    if "=" not in line:
        return line

    key_part, value = line.split("=", 1)
    key = key_part.strip()

    if not key:
        return line

    if _is_secret_key(key):
        return f"{key_part}={REDACTED_VALUE}"

    return line


def _redact_assignment_lines(content: str) -> str:
    return "\n".join(_redact_assignment_line(line) for line in content.split("\n"))


def _redact_assignment_line(line: str) -> str:
    stripped = line.lstrip()
    if not stripped or stripped.startswith(("#", "!", "//")):
        return line
    match = _ASSIGNMENT_RE.match(line)
    if not match or not _is_secret_key(match.group("key").strip()):
        return line
    value = match.group("value").lstrip()
    value_quote = value[0] if value[:1] in {"\"", "'"} else match.group("quote")
    return (
        f"{match.group('indent')}{match.group('quote')}{match.group('key')}"
        f"{match.group('quote')}{match.group('separator')}"
        f"{value_quote}{REDACTED_VALUE}{value_quote}"
        f"{match.group('comma')}"
    )


def _redact_json(content: str) -> str:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return _redact_assignment_lines(content)
    redacted, changed = _redact_json_values(parsed)
    if not changed:
        return content
    trailing_newline = "\n" if content.endswith("\n") else ""
    return json.dumps(redacted, ensure_ascii=False, indent=2) + trailing_newline


def _redact_json_values(value: object) -> tuple[object, bool]:
    if isinstance(value, dict):
        changed = False
        redacted: dict[str, object] = {}
        for key, item in value.items():
            if _is_secret_key(str(key)):
                redacted[key] = REDACTED_VALUE
                changed = True
                continue
            child, child_changed = _redact_json_values(item)
            redacted[key] = child
            changed = changed or child_changed
        return redacted, changed
    if isinstance(value, list):
        changed = False
        redacted_list: list[object] = []
        for item in value:
            child, child_changed = _redact_json_values(item)
            redacted_list.append(child)
            changed = changed or child_changed
        return redacted_list, changed
    return value, False


def _is_secret_key(key: str) -> bool:
    if key in _EXPLICIT_SECRET_KEYS:
        return True
    key_lower = key.lower()
    return any(pattern in key_lower for pattern in _SECRET_KEY_PATTERNS)


def redact_for_display(value: str | None) -> str | None:
    if value is None:
        return None
    return REDACTED_VALUE


def file_extension(path: str) -> str:
    return Path(path).suffix.lower()
