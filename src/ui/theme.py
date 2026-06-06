from __future__ import annotations


BG = "#0d1117"
PANEL = "#151b23"
PANEL_SOFT = "#10161d"
PANEL_RAISED = "#1b232d"
LINE = "#2a3441"
LINE_STRONG = "#3b4757"
TEXT = "#e6edf3"
MUTED = "#9aa7b5"
SOFT_TEXT = "#748293"
BLUE = "#4f8cff"
BLUE_SOFT = "#13243d"
GREEN = "#43c386"
GREEN_SOFT = "#10271f"
AMBER = "#e3a341"
AMBER_SOFT = "#2c2110"
RED = "#f06a5f"
RED_SOFT = "#351817"
VIOLET = "#9b8cff"
INPUT_BG = "#0f151c"
TAB_BG = "#111821"
RADIUS = 8


def risk_color(level: str) -> tuple[str, str]:
    normalized = level.upper()
    if normalized == "UNREVIEWED":
        return MUTED, PANEL_SOFT
    if normalized == "LOW":
        return GREEN, GREEN_SOFT
    if normalized == "MEDIUM":
        return AMBER, AMBER_SOFT
    return RED, RED_SOFT


def log_color(level: str) -> tuple[str, str]:
    normalized = level.upper()
    if normalized == "INFO":
        return BLUE, BLUE_SOFT
    if normalized == "WARN":
        return AMBER, AMBER_SOFT
    if normalized == "ERROR":
        return RED, RED_SOFT
    return MUTED, PANEL_SOFT
