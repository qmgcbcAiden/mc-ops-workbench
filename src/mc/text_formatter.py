from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FormatResult:
    content: str
    language: str
    supported: bool
    changed: bool
    error: str | None = None


_LANGUAGE_BY_EXTENSION = {
    ".bat": "batch",
    ".cfg": "properties",
    ".conf": "config",
    ".ini": "ini",
    ".json": "json",
    ".json5": "json5",
    ".mcmeta": "json",
    ".properties": "properties",
    ".sh": "shell",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
}

_LANGUAGE_BY_FILENAME = {
    "eula.txt": "properties",
}


def language_for_path(relative_path: str) -> str:
    path = Path(relative_path)
    filename = path.name.lower()
    if filename in _LANGUAGE_BY_FILENAME:
        return _LANGUAGE_BY_FILENAME[filename]
    suffix = path.suffix.lower()
    return _LANGUAGE_BY_EXTENSION.get(suffix, suffix.lstrip(".") or "text")


def format_text(relative_path: str, content: str) -> FormatResult:
    language = language_for_path(relative_path)
    normalized = _normalize_newlines(content)

    try:
        if language == "json":
            formatted = _format_json(normalized)
        elif language in {"properties", "ini", "config"}:
            formatted = _format_assignment_config(normalized, language)
        elif language == "yaml":
            formatted = _format_yaml(normalized)
        elif language == "toml":
            formatted = _format_toml(normalized)
        elif language == "json5":
            formatted = _format_json5(normalized)
        else:
            return FormatResult(
                content=content,
                language=language,
                supported=False,
                changed=False,
            )
    except ValueError as exc:
        return FormatResult(
            content=content,
            language=language,
            supported=True,
            changed=False,
            error=str(exc),
        )

    return FormatResult(
        content=formatted,
        language=language,
        supported=True,
        changed=formatted != content,
    )


def _normalize_newlines(content: str) -> str:
    return content.replace("\r\n", "\n").replace("\r", "\n")


def _final_newline(lines: list[str]) -> str:
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + ("\n" if lines else "")


def _format_json(content: str) -> str:
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 格式错误：第 {exc.lineno} 行第 {exc.colno} 列 - {exc.msg}") from exc
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _format_assignment_config(content: str, language: str) -> str:
    lines: list[str] = []
    for line in content.split("\n"):
        stripped = line.strip()
        if not stripped:
            lines.append("")
            continue
        if stripped.startswith(("#", "!", ";")):
            lines.append(line.rstrip())
            continue
        if language == "ini" and stripped.startswith("[") and stripped.endswith("]"):
            lines.append(stripped)
            continue

        match = re.match(r"^(\s*)([^=:#]+?)\s*([=:])\s*(.*?)(\s*)$", line)
        if match:
            indent, key, _separator, value, _trailing = match.groups()
            separator = " = " if language in {"ini", "config"} else "="
            lines.append(f"{indent}{key.strip()}{separator}{value.rstrip()}")
        else:
            lines.append(line.rstrip())
    return _final_newline(lines)


def _format_yaml(content: str) -> str:
    lines: list[str] = []
    for raw_line in content.split("\n"):
        line = raw_line.rstrip()
        leading = len(line) - len(line.lstrip("\t"))
        if leading:
            line = ("  " * leading) + line[leading:]
        stripped = line.lstrip()
        indent = line[: len(line) - len(stripped)]
        if not stripped or stripped.startswith("#") or stripped in {"---", "..."}:
            lines.append(line)
            continue
        if stripped.startswith("-"):
            tail = stripped[1:].lstrip()
            lines.append(f"{indent}- {tail}" if tail else f"{indent}-")
            continue
        match = re.match(r"^([^:#][^:]*?):(?:\s*)(.*)$", stripped)
        if match and not stripped.startswith(("{", "[")):
            key, value = match.groups()
            lines.append(f"{indent}{key.rstrip()}: {value}" if value else f"{indent}{key.rstrip()}:")
            continue
        lines.append(line)
    return _final_newline(lines)


def _format_toml(content: str) -> str:
    try:
        import tomllib

        tomllib.loads(content)
    except Exception as exc:
        raise ValueError(f"TOML 格式错误：{exc}") from exc

    lines: list[str] = []
    for line in content.split("\n"):
        stripped = line.strip()
        if not stripped:
            lines.append("")
        elif stripped.startswith("#"):
            lines.append(line.rstrip())
        elif stripped.startswith("[") and stripped.endswith("]"):
            lines.append(stripped)
        else:
            match = re.match(r"^(\s*)([^=]+?)\s*=\s*(.*?)(\s*)$", line)
            if match:
                indent, key, value, _trailing = match.groups()
                lines.append(f"{indent}{key.strip()} = {value.rstrip()}")
            else:
                lines.append(line.rstrip())
    return _final_newline(lines)


def _format_json5(content: str) -> str:
    lines: list[str] = []
    for line in content.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("//"):
            lines.append(line.rstrip())
            continue
        lines.append(line.rstrip())
    return _final_newline(lines)
