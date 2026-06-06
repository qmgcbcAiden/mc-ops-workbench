from __future__ import annotations

import asyncio

from src.ui.components.code_viewer import CodeViewer


def test_review_viewer_renders_inline_removed_and_added_lines() -> None:
    viewer = CodeViewer(
        content="# server\nmax-players=20\npvp=true\n",
        language="properties",
        original_content="# server\nmax-players=10\npvp=true\n",
    )

    viewer.build()

    changed = [
        (line.marker, line.old_number, line.new_number, line.text)
        for line in viewer.inline_lines
        if line.marker != " "
    ]
    assert changed == [
        ("-", 2, None, "max-players=10"),
        ("+", None, 2, "max-players=20"),
    ]
    assert viewer.first_change_scroll_key == "inline-change-1"


def test_review_viewer_scrolls_to_first_change() -> None:
    viewer = CodeViewer(
        content="one\ntwo changed\nthree\n",
        original_content="one\ntwo\nthree\n",
    )
    viewer.build()
    calls: list[dict] = []

    async def capture_scroll(**kwargs) -> None:
        calls.append(kwargs)

    viewer._scrollable.scroll_to = capture_scroll

    asyncio.run(viewer.scroll_to_first_change())

    assert calls == [{"scroll_key": "inline-change-1", "duration": 160}]
