from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SyntaxToken:
    text: str
    role: str = "plain"


_VALUE_PATTERN = re.compile(
    r'("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|'
    r'\b(?:true|false|null|yes|no|on|off)\b|'
    r'\b-?(?:0x[0-9a-fA-F]+|\d+(?:\.\d+)?)\b|'
    r'[{}\[\],])',
    re.IGNORECASE,
)
_JSON_PATTERN = re.compile(
    r'("(?:\\.|[^"\\])*"|'
    r'\b(?:true|false|null)\b|'
    r'\b-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b|'
    r'[{}\[\],:])',
    re.IGNORECASE,
)
_SHELL_PATTERN = re.compile(
    r'("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|'
    r'\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|'
    r'--?[A-Za-z0-9][A-Za-z0-9_-]*|'
    r'\b(?:if|then|else|elif|fi|for|while|do|done|case|esac|in|function|'
    r'export|set|unset|cd|echo|java|exec|exit)\b|'
    r'\b-?(?:0x[0-9a-fA-F]+|\d+(?:\.\d+)?)\b|'
    r'[|&;<>()])',
    re.IGNORECASE,
)
_BATCH_PATTERN = re.compile(
    r'("(?:\\.|[^"\\])*"|'
    r'%[A-Za-z0-9_]+%|%[*0-9]|'
    r'--?[A-Za-z0-9][A-Za-z0-9_-]*|'
    r'\b(?:if|else|for|in|do|set|setlocal|endlocal|call|goto|echo|java|'
    r'exit|pause|rem)\b|'
    r'\b-?(?:0x[0-9a-fA-F]+|\d+(?:\.\d+)?)\b|'
    r'[|&<>():])',
    re.IGNORECASE,
)


def tokenize_line(line: str, language: str) -> list[SyntaxToken]:
    normalized = (language or "text").lower()
    stripped = line.lstrip()
    if stripped.startswith(("#", "!", ";")) and normalized in {
        "properties", "config", "ini", "toml", "yaml"
    }:
        return [SyntaxToken(line, "comment")]
    if stripped.startswith("//") and normalized == "json5":
        return [SyntaxToken(line, "comment")]
    if stripped.startswith("#") and normalized == "shell":
        return [SyntaxToken(line, "comment")]
    if normalized == "batch" and (
        stripped.lower().startswith("rem ") or stripped.startswith("::")
    ):
        return [SyntaxToken(line, "comment")]

    if normalized in {"json", "json5"}:
        return _lex_json(line)
    if normalized in {"properties", "config", "ini", "toml"}:
        return _lex_assignment(line, normalized)
    if normalized == "yaml":
        return _lex_yaml(line)
    if normalized == "shell":
        return _lex_script(line, _SHELL_PATTERN)
    if normalized == "batch":
        return _lex_script(line, _BATCH_PATTERN)
    if normalized == "markdown":
        if stripped.startswith("#"):
            return [SyntaxToken(line, "heading")]
        if stripped.startswith("```"):
            return [SyntaxToken(line, "keyword")]
    return [SyntaxToken(line)]


def _lex_json(line: str) -> list[SyntaxToken]:
    tokens: list[SyntaxToken] = []
    cursor = 0
    for match in _JSON_PATTERN.finditer(line):
        if match.start() > cursor:
            tokens.append(SyntaxToken(line[cursor:match.start()]))
        value = match.group(0)
        role = _value_role(value)
        if value.startswith('"') and re.match(r"\s*:", line[match.end():]):
            role = "key"
        elif value == ":":
            role = "operator"
        tokens.append(SyntaxToken(value, role))
        cursor = match.end()
    if cursor < len(line):
        tokens.append(SyntaxToken(line[cursor:]))
    return tokens or [SyntaxToken(line)]


def _lex_assignment(line: str, language: str) -> list[SyntaxToken]:
    stripped = line.strip()
    if language in {"ini", "toml"} and stripped.startswith("[") and stripped.endswith("]"):
        return [SyntaxToken(line, "section")]
    match = re.match(r"^(\s*)([^=:]+?)(\s*[=:]\s*)(.*)$", line)
    if not match:
        return _lex_value(line)
    indent, key, operator, value = match.groups()
    tokens: list[SyntaxToken] = []
    if indent:
        tokens.append(SyntaxToken(indent))
    tokens.extend([
        SyntaxToken(key.rstrip(), "key"),
        SyntaxToken(operator, "operator"),
    ])
    tokens.extend(_lex_value(value))
    return tokens


def _lex_yaml(line: str) -> list[SyntaxToken]:
    match = re.match(r"^(\s*)(-\s+)?([^:#][^:]*?)(:\s*)(.*)$", line)
    if not match:
        return _lex_value(line)
    indent, list_marker, key, operator, value = match.groups()
    tokens: list[SyntaxToken] = []
    if indent:
        tokens.append(SyntaxToken(indent))
    if list_marker:
        tokens.append(SyntaxToken(list_marker, "operator"))
    tokens.extend([
        SyntaxToken(key, "key"),
        SyntaxToken(operator, "operator"),
    ])
    tokens.extend(_lex_value(value))
    return tokens


def _lex_value(value: str) -> list[SyntaxToken]:
    tokens: list[SyntaxToken] = []
    cursor = 0
    for match in _VALUE_PATTERN.finditer(value):
        if match.start() > cursor:
            tokens.append(SyntaxToken(value[cursor:match.start()]))
        text = match.group(0)
        tokens.append(SyntaxToken(text, _value_role(text)))
        cursor = match.end()
    if cursor < len(value):
        tokens.append(SyntaxToken(value[cursor:]))
    return tokens or [SyntaxToken(value)]


def _lex_script(line: str, pattern: re.Pattern[str]) -> list[SyntaxToken]:
    tokens: list[SyntaxToken] = []
    cursor = 0
    for match in pattern.finditer(line):
        if match.start() > cursor:
            tokens.append(SyntaxToken(line[cursor:match.start()]))
        text = match.group(0)
        tokens.append(SyntaxToken(text, _script_role(text)))
        cursor = match.end()
    if cursor < len(line):
        tokens.append(SyntaxToken(line[cursor:]))
    return tokens or [SyntaxToken(line)]


def _value_role(value: str) -> str:
    lowered = value.lower()
    if value.startswith(("\"", "'")):
        return "string"
    if lowered in {"true", "false", "null", "yes", "no", "on", "off"}:
        return "keyword"
    if re.fullmatch(r"-?(?:0x[0-9a-fA-F]+|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)", value):
        return "number"
    if value in "{}[],:":
        return "operator"
    return "plain"


def _script_role(value: str) -> str:
    lowered = value.lower()
    if value.startswith(("\"", "'")):
        return "string"
    if value.startswith("$") or value.startswith("%"):
        return "key"
    if lowered in {
        "if", "then", "else", "elif", "fi", "for", "while", "do", "done",
        "case", "esac", "in", "function", "export", "set", "unset", "cd",
        "echo", "java", "exec", "exit", "setlocal", "endlocal", "call",
        "goto", "pause", "rem",
    }:
        return "keyword"
    if re.fullmatch(r"-?(?:0x[0-9a-fA-F]+|\d+(?:\.\d+)?)", value):
        return "number"
    if value.startswith("-") or value in "|&;<>():":
        return "operator"
    return "plain"
