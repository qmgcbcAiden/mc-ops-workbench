from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass
class PropertyLine:
    kind: Literal["entry", "comment", "blank", "raw"]
    key: str | None
    value: str | None
    raw: str


@dataclass
class PropertiesDocument:
    lines: list[PropertyLine]

    def get(self, key: str) -> str | None:
        for line in reversed(self.lines):
            if line.kind == "entry" and line.key == key:
                return line.value
        return None

    def entry_count(self, key: str) -> int:
        return sum(1 for line in self.lines if line.kind == "entry" and line.key == key)

    def set(self, key: str, value: str) -> None:
        replacement = f"{key}={value}"
        for line in reversed(self.lines):
            if line.kind == "entry" and line.key == key:
                line.value = value
                line.raw = replacement
                return

        if self.lines and self.lines[-1].kind != "blank":
            self.lines.append(PropertyLine(kind="blank", key=None, value=None, raw=""))
        self.lines.append(
            PropertyLine(
                kind="comment",
                key=None,
                value=None,
                raw=f"# Added by MC ops dashboard: {key}",
            )
        )
        self.lines.append(PropertyLine(kind="entry", key=key, value=value, raw=replacement))

    def to_text(self) -> str:
        return "\n".join(line.raw for line in self.lines) + "\n"


def parse_properties(text: str) -> PropertiesDocument:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    raw_lines = normalized.split("\n")
    if raw_lines and raw_lines[-1] == "":
        raw_lines = raw_lines[:-1]

    lines = [_parse_line(raw_line) for raw_line in raw_lines]
    return PropertiesDocument(lines=lines)


def _parse_line(raw: str) -> PropertyLine:
    stripped = raw.strip()
    if stripped == "":
        return PropertyLine(kind="blank", key=None, value=None, raw=raw)
    if stripped.startswith("#") or stripped.startswith("!"):
        return PropertyLine(kind="comment", key=None, value=None, raw=raw)
    if "=" not in raw:
        return PropertyLine(kind="raw", key=None, value=None, raw=raw)

    key_part, value = raw.split("=", 1)
    key = key_part.strip()
    if not key:
        return PropertyLine(kind="raw", key=None, value=None, raw=raw)
    return PropertyLine(kind="entry", key=key, value=value.strip(), raw=raw)
