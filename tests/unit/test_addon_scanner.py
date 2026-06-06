from __future__ import annotations

import json
import zipfile
from pathlib import Path

from src.mc.addon_scanner import (
    diagnose_addons,
    normalize_severity,
    scan_local_addons,
)


def test_detects_fabric_client_only_mod_as_blocker(tmp_path: Path) -> None:
    server_dir = tmp_path / "server"
    mods_dir = server_dir / "mods"
    mods_dir.mkdir(parents=True)
    (server_dir / "fabric-1.20.1.jar").write_bytes(b"server")
    _jar(
        mods_dir / "ClientMap.jar",
        {
            "fabric.mod.json": json.dumps({
                "schemaVersion": 1,
                "id": "clientmap",
                "name": "Client Map",
                "version": "1.0.0",
                "environment": "client",
                "depends": {"minecraft": "1.20.1"},
            }),
        },
    )

    assets, core = scan_local_addons(server_dir, server_dir / "fabric-1.20.1.jar")
    diagnostics = [item.to_dict() for item in diagnose_addons(assets, core, server_dir)]

    assert any(
        item["severity"] == "BLOCKER"
        and item["category"] == "client_only_mod"
        and item["evidence_type"] == "metadata"
        for item in diagnostics
    )


def test_detects_missing_fabric_dependency_from_dependency_graph(tmp_path: Path) -> None:
    server_dir = tmp_path / "server"
    mods_dir = server_dir / "mods"
    mods_dir.mkdir(parents=True)
    (server_dir / "fabric-1.20.1.jar").write_bytes(b"server")
    _jar(
        mods_dir / "NeedsLibrary.jar",
        {
            "fabric.mod.json": json.dumps({
                "id": "needslibrary",
                "version": "1.0.0",
                "depends": {"minecraft": "1.20.1", "examplelib": ">=2.0.0"},
            }),
        },
    )

    assets, core = scan_local_addons(server_dir, server_dir / "fabric-1.20.1.jar")
    diagnostics = [item.to_dict() for item in diagnose_addons(assets, core, server_dir)]

    assert any(
        item["severity"] == "BLOCKER"
        and item["category"] == "missing_dependency"
        and item["evidence_type"] == "dependency_graph"
        for item in diagnostics
    )


def test_detects_plugin_hard_dependency_from_plugin_yml(tmp_path: Path) -> None:
    server_dir = tmp_path / "server"
    plugins_dir = server_dir / "plugins"
    plugins_dir.mkdir(parents=True)
    (server_dir / "paper-1.20.1.jar").write_bytes(b"server")
    _jar(
        plugins_dir / "Shop.jar",
        {
            "plugin.yml": "\n".join([
                "name: Shop",
                "version: 1.0.0",
                "depend: [Vault]",
            ]),
        },
    )

    assets, core = scan_local_addons(server_dir, server_dir / "paper-1.20.1.jar")
    diagnostics = [item.to_dict() for item in diagnose_addons(assets, core, server_dir)]

    assert any(
        item["severity"] == "BLOCKER"
        and item["category"] == "missing_dependency"
        and item["evidence_type"] == "dependency_graph"
        for item in diagnostics
    )


def test_heuristic_evidence_cannot_create_blocker() -> None:
    assert normalize_severity("BLOCKER", "heuristic_filename") == "MEDIUM"
    assert normalize_severity("HIGH", "heuristic_filename") == "MEDIUM"


def _jar(path: Path, files: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
