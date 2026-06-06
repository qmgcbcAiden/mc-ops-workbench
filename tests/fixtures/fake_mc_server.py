from __future__ import annotations

import os
from pathlib import Path


def create_fake_mc_server(root: Path) -> dict:
    (root / "server.jar").write_bytes(b"\x00\x01\x02FAKE JAR")
    (root / "server.properties").write_text(
        "server-port=25565\nmotd=Fake Server\n", encoding="utf-8"
    )
    (root / "ops.json").write_text('[{"name":"TestOp"}]\n', encoding="utf-8")
    (root / "eula.txt").write_text("eula=true\n", encoding="utf-8")

    logs_dir = root / "logs"
    logs_dir.mkdir()
    (logs_dir / "latest.log").write_text(
        "[13:04:12 INFO]: Server started\n"
        "[13:05:00 WARN]: Test warning\n"
        "[13:06:00 ERROR]: Test error\n",
        encoding="utf-8",
    )

    world_dir = root / "world"
    world_dir.mkdir()
    (world_dir / "level.dat").write_text("fake level data", encoding="utf-8")

    plugins_dir = root / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "TestPlugin.jar").write_bytes(b"\x00\x01PLUGIN")

    return {
        "root": root,
        "jar": root / "server.jar",
        "properties": root / "server.properties",
        "log_path": logs_dir / "latest.log",
    }
