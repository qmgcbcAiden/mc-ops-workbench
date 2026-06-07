from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from src.config.settings import Settings
from src.mc.addon_scanner import (
    AddonAsset,
    AddonDiagnostic,
    diagnose_addons,
    diagnose_runtime_logs,
    normalize_addon_key,
    scan_local_addons,
)
from src.repositories._time import utc_now_iso
from src.repositories.addon_diagnostic_repository import AddonDiagnosticRepository
from src.service.log_service import LogService


class AddonKnowledgeProvider(Protocol):
    def enrich_assets(self, assets: list[AddonAsset]) -> list[AddonDiagnostic]:
        ...


class AddonDiagnosticService:
    def __init__(
        self,
        settings: Settings,
        repository: AddonDiagnosticRepository,
        log_service: LogService | None = None,
        knowledge_provider: AddonKnowledgeProvider | None = None,
    ) -> None:
        self._settings = settings
        self._repo = repository
        self._log_service = log_service
        self._knowledge_provider = knowledge_provider or AddonKnowledgeService(settings, repository)

    def scan_addons(self, refresh_online: bool = False) -> dict[str, Any]:
        scan_run_id = f"addons_{uuid4().hex[:16]}"
        self._repo.create_scan_run(scan_run_id, refresh_online=refresh_online)
        assets: list[AddonAsset] = []
        diagnostics: list[AddonDiagnostic] = []
        try:
            assets, core = scan_local_addons(
                self._settings.mc_server_dir,
                self._settings.mc_server_jar,
                max_jar_bytes=self._settings.addon_scan_max_jar_bytes,
            )
            if refresh_online and self._settings.addon_knowledge_online_enabled:
                diagnostics.extend(self._knowledge_provider.enrich_assets(assets))
            diagnostics.extend(diagnose_addons(assets, core, self._settings.mc_server_dir))
            diagnostics.extend(self._runtime_log_diagnostics())

            asset_rows = [_asset_row(scan_run_id, asset) for asset in assets]
            diagnostic_rows = [
                _diagnostic_row(scan_run_id, diagnostic)
                for diagnostic in _dedupe_diagnostics(diagnostics)
            ]
            self._repo.add_assets(scan_run_id, asset_rows)
            self._repo.add_diagnostics(scan_run_id, diagnostic_rows)
            severity_counts = _severity_counts(diagnostic_rows)
            self._repo.finish_scan_run(
                scan_run_id,
                status="completed",
                asset_count=len(asset_rows),
                diagnostic_count=len(diagnostic_rows),
                severity_counts=severity_counts,
            )
            return self.get_latest_addon_report()
        except Exception as exc:
            self._repo.finish_scan_run(
                scan_run_id,
                status="failed",
                asset_count=len(assets),
                diagnostic_count=0,
                severity_counts={},
                error_message=str(exc),
            )
            return {
                "status": "failed",
                "scan_run_id": scan_run_id,
                "error_message": str(exc),
                "message": "组件诊断扫描失败。",
            }

    def get_latest_addon_report(self) -> dict[str, Any]:
        scan = self._repo.latest_scan_run()
        if scan is None:
            return {
                "status": "no_scan",
                "message": "尚未执行组件诊断扫描。",
                "scan": None,
                "assets": [],
                "diagnostics": [],
                "summary": _summary_from_counts({}),
            }
        assets = self._repo.list_assets(str(scan["id"]))
        diagnostics = self._repo.list_diagnostics(str(scan["id"]))
        return {
            "status": scan.get("status", "unknown"),
            "scan": scan,
            "scan_run_id": scan["id"],
            "assets": assets,
            "diagnostics": diagnostics,
            "summary": _summary_from_counts({
                "BLOCKER": scan.get("blocker_count", 0),
                "HIGH": scan.get("high_count", 0),
                "MEDIUM": scan.get("medium_count", 0),
                "LOW": scan.get("low_count", 0),
                "INFO": scan.get("info_count", 0),
            }),
            "message": _report_message(scan, len(assets), diagnostics),
        }

    def list_addon_assets(self) -> list[dict]:
        return self._repo.list_assets()

    def list_addon_diagnostics(self, severity: str | None = None) -> list[dict]:
        return self._repo.list_diagnostics(severity=severity)

    def create_addon_remediation_plan(self, diagnostic_ids: list[str]) -> dict[str, Any]:
        latest = self._repo.latest_scan_run()
        if latest is None:
            return {
                "status": "failed",
                "message": "尚未执行组件诊断扫描，无法生成修复草案。",
            }
        available = {
            diagnostic["id"]: diagnostic
            for diagnostic in self._repo.list_diagnostics(str(latest["id"]))
        }
        selected = [
            available[diagnostic_id]
            for diagnostic_id in diagnostic_ids
            if diagnostic_id in available
        ]
        if not selected:
            return {
                "status": "failed",
                "message": "未找到可生成修复草案的诊断项。",
                "diagnostic_ids": diagnostic_ids,
            }
        actions = _remediation_actions(selected)
        proposal_id = f"addr_{uuid4().hex[:16]}"
        self._repo.create_remediation_proposal(
            proposal_id,
            scan_run_id=str(latest["id"]),
            diagnostic_ids=[diagnostic["id"] for diagnostic in selected],
            actions=actions,
        )
        return {
            "status": "proposal_created",
            "proposal_id": proposal_id,
            "scan_run_id": latest["id"],
            "diagnostic_ids": [diagnostic["id"] for diagnostic in selected],
            "actions": actions,
            "confirmation_required": True,
            "message": "已生成组件修复草案。首版只创建草案，不会自动移动、删除或修改文件。",
        }

    def create_runtime_validation_plan(self) -> dict[str, Any]:
        latest = self._repo.latest_scan_run()
        return {
            "status": "confirmation_required",
            "action_type": "addon_runtime_validation",
            "scan_run_id": latest["id"] if latest else None,
            "max_rounds": 3,
            "log_follow_seconds": 120,
            "restart_required": True,
            "confirmation_required": True,
            "message": (
                "运行时验证需要用户确认启动或重启服务器；确认后只读取启动窗口日志作为 runtime_log 证据，"
                "不会自动移动、删除、禁用或编辑 jar。"
            ),
        }

    def _runtime_log_diagnostics(self) -> list[AddonDiagnostic]:
        if self._log_service is None:
            return []
        try:
            events = self._log_service.list_recent(level="ANY", limit=120)
        except Exception:
            return []
        return diagnose_runtime_logs(events)


class AddonKnowledgeService:
    def __init__(
        self,
        settings: Settings,
        repository: AddonDiagnosticRepository,
        opener: Any | None = None,
    ) -> None:
        self._settings = settings
        self._repo = repository
        self._opener = opener or urllib.request.urlopen

    def enrich_assets(self, assets: list[AddonAsset]) -> list[AddonDiagnostic]:
        diagnostics: list[AddonDiagnostic] = []
        if self._settings.modrinth_enabled:
            for asset in assets:
                response = self._lookup_modrinth(asset)
                if response:
                    _apply_modrinth_response(asset, response)
        if self._settings.curseforge_api_key:
            for asset in assets:
                response = self._lookup_curseforge(asset)
                if response:
                    _apply_curseforge_response(asset, response)
        diagnostics.extend(_diagnose_api_knowledge(assets, self._settings.mc_server_jar))
        return diagnostics

    def _lookup_modrinth(self, asset: AddonAsset) -> dict[str, Any] | None:
        if not asset.sha1:
            return None
        cache_key = f"modrinth:sha1:{asset.sha1}"
        cached = self._repo.get_cache(cache_key, utc_now_iso())
        if cached:
            return cached.get("response")
        request = {"hash_algorithm": "sha1", "sha1": asset.sha1}
        started = time.monotonic()
        try:
            version = self._request_json(
                f"https://api.modrinth.com/v2/version_file/{asset.sha1}?algorithm=sha1",
                provider="modrinth",
            )
            project_id = version.get("project_id")
            project = (
                self._request_json(
                    f"https://api.modrinth.com/v2/project/{project_id}",
                    provider="modrinth",
                )
                if project_id
                else {}
            )
            response = {"version": version, "project": project}
            self._repo.upsert_cache(
                cache_key=cache_key,
                provider="modrinth",
                request=request,
                response=response,
                status="ok",
                expires_at=_expires_at(self._settings.addon_knowledge_cache_ttl_hours),
            )
            self._repo.record_external_request(
                provider="modrinth",
                request_type="version_file_hash",
                request=request,
                status="ok",
                latency_ms=_elapsed_ms(started),
            )
            return response
        except Exception as exc:
            self._repo.record_external_request(
                provider="modrinth",
                request_type="version_file_hash",
                request=request,
                status="failed",
                error_message=str(exc),
                latency_ms=_elapsed_ms(started),
            )
            return None

    def _lookup_curseforge(self, asset: AddonAsset) -> dict[str, Any] | None:
        source_path = self._settings.mc_server_dir / asset.relative_path
        fingerprint = _curseforge_fingerprint(source_path)
        if fingerprint is None:
            return None
        cache_key = f"curseforge:fingerprint:{fingerprint}"
        cached = self._repo.get_cache(cache_key, utc_now_iso())
        if cached:
            return cached.get("response")
        request = {"fingerprints": [fingerprint]}
        started = time.monotonic()
        try:
            response = self._request_json(
                "https://api.curseforge.com/v1/fingerprints",
                provider="curseforge",
                method="POST",
                body=request,
                extra_headers={"x-api-key": self._settings.curseforge_api_key},
            )
            self._repo.upsert_cache(
                cache_key=cache_key,
                provider="curseforge",
                request=request,
                response=response,
                status="ok",
                expires_at=_expires_at(self._settings.addon_knowledge_cache_ttl_hours),
            )
            self._repo.record_external_request(
                provider="curseforge",
                request_type="fingerprints",
                request=request,
                status="ok",
                latency_ms=_elapsed_ms(started),
            )
            return response
        except Exception as exc:
            self._repo.record_external_request(
                provider="curseforge",
                request_type="fingerprints",
                request=request,
                status="failed",
                error_message=str(exc),
                latency_ms=_elapsed_ms(started),
            )
            return None

    def _request_json(
        self,
        url: str,
        *,
        provider: str,
        method: str = "GET",
        body: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        headers = {
            "User-Agent": "mvp-codex-mc-addon-diagnostics/1.0",
            "Accept": "application/json",
        }
        headers.update(extra_headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._opener(request, timeout=12) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {}
            raise RuntimeError(f"{provider} returned HTTP {exc.code}") from exc
        return json.loads(raw.decode("utf-8"))


def _asset_row(scan_run_id: str, asset: AddonAsset) -> dict[str, Any]:
    row = asset.to_dict()
    identity = json.dumps(
        {
            "scan_run_id": scan_run_id,
            "sha1": row.get("sha1"),
            "relative_path": row["relative_path"],
        },
        sort_keys=True,
    )
    row["id"] = f"adda_{_short_hash(identity)}"
    return row


def _diagnostic_row(scan_run_id: str, diagnostic: AddonDiagnostic) -> dict[str, Any]:
    row = diagnostic.to_dict()
    identity = json.dumps(
        {
            "scan_run_id": scan_run_id,
            "category": row["category"],
            "message": row["message"],
            "affected_files": row["affected_files"],
            "evidence_type": row["evidence_type"],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    row["id"] = f"addd_{_short_hash(identity)}"
    return row


def _dedupe_diagnostics(diagnostics: list[AddonDiagnostic]) -> list[AddonDiagnostic]:
    seen: set[str] = set()
    unique: list[AddonDiagnostic] = []
    for diagnostic in diagnostics:
        row = diagnostic.to_dict()
        key = json.dumps(
            {
                "severity": row["severity"],
                "category": row["category"],
                "message": row["message"],
                "affected_files": row["affected_files"],
                "evidence_type": row["evidence_type"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(diagnostic)
    return unique


def _severity_counts(diagnostics: list[dict[str, Any]]) -> dict[str, int]:
    counts = {severity: 0 for severity in ("BLOCKER", "HIGH", "MEDIUM", "LOW", "INFO")}
    for diagnostic in diagnostics:
        severity = str(diagnostic.get("severity", "INFO")).upper()
        counts[severity if severity in counts else "INFO"] += 1
    return counts


def _summary_from_counts(counts: dict[str, Any]) -> dict[str, int]:
    return {
        "blockers": int(counts.get("BLOCKER", 0) or 0),
        "high": int(counts.get("HIGH", 0) or 0),
        "medium": int(counts.get("MEDIUM", 0) or 0),
        "low": int(counts.get("LOW", 0) or 0),
        "info": int(counts.get("INFO", 0) or 0),
    }


def _report_message(scan: dict[str, Any], asset_count: int, diagnostics: list[dict]) -> str:
    if scan.get("status") == "failed":
        return scan.get("error_message") or "组件诊断扫描失败。"
    blockers = int(scan.get("blocker_count") or 0)
    high = int(scan.get("high_count") or 0)
    if blockers:
        return f"已扫描 {asset_count} 个组件，发现 {blockers} 个阻断问题。"
    if high:
        return f"已扫描 {asset_count} 个组件，发现 {high} 个高风险问题。"
    if diagnostics:
        return f"已扫描 {asset_count} 个组件，发现 {len(diagnostics)} 条诊断。"
    return f"已扫描 {asset_count} 个组件，未发现硬证据冲突。"


def _remediation_actions(diagnostics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for diagnostic in diagnostics:
        for action in diagnostic.get("suggested_actions", []):
            if not isinstance(action, dict):
                continue
            key = json.dumps(action, sort_keys=True, ensure_ascii=False)
            if key in seen:
                continue
            seen.add(key)
            action = dict(action)
            action["confirmation_required"] = True
            action["execution_status"] = "not_executed"
            actions.append(action)
    if not actions:
        actions.append({
            "type": "manual_review",
            "label": "人工检查诊断项并选择兼容版本",
            "confirmation_required": True,
            "execution_status": "not_executed",
        })
    return actions


def _apply_modrinth_response(asset: AddonAsset, response: dict[str, Any]) -> None:
    version = response.get("version") if isinstance(response.get("version"), dict) else {}
    project = response.get("project") if isinstance(response.get("project"), dict) else {}
    if not version and not project:
        return
    game_versions = version.get("game_versions") if isinstance(version.get("game_versions"), list) else []
    loaders = version.get("loaders") if isinstance(version.get("loaders"), list) else []
    dependencies = version.get("dependencies") if isinstance(version.get("dependencies"), list) else []
    if game_versions:
        asset.minecraft_versions = sorted(set(asset.minecraft_versions + [str(item) for item in game_versions]))
    if not asset.loader and loaders:
        asset.loader = str(loaders[0])
    asset.knowledge["modrinth"] = {
        "project_id": project.get("id") or version.get("project_id"),
        "slug": project.get("slug"),
        "title": project.get("title"),
        "client_side": project.get("client_side"),
        "server_side": project.get("server_side"),
        "game_versions": game_versions,
        "loaders": loaders,
        "dependencies": dependencies,
        "source": "api_hash_match",
    }


def _apply_curseforge_response(asset: AddonAsset, response: dict[str, Any]) -> None:
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    matches = data.get("exactMatches") if isinstance(data.get("exactMatches"), list) else []
    if not matches:
        return
    match = matches[0]
    if not isinstance(match, dict):
        return
    file_info = match.get("file") if isinstance(match.get("file"), dict) else {}
    mod_info = match.get("mod") if isinstance(match.get("mod"), dict) else {}
    asset.knowledge["curseforge"] = {
        "mod_id": mod_info.get("id"),
        "name": mod_info.get("name"),
        "file_id": file_info.get("id"),
        "game_versions": file_info.get("gameVersions") or [],
        "source": "api_hash_match",
    }


def _diagnose_api_knowledge(
    assets: list[AddonAsset],
    server_jar: Path,
) -> list[AddonDiagnostic]:
    diagnostics: list[AddonDiagnostic] = []
    server_version = _version_from_server_jar(server_jar)
    modrinth_project_ids = {
        str(asset.knowledge.get("modrinth", {}).get("project_id"))
        for asset in assets
        if asset.knowledge.get("modrinth", {}).get("project_id")
    }
    for asset in assets:
        modrinth = asset.knowledge.get("modrinth", {})
        if isinstance(modrinth, dict) and modrinth:
            server_side = str(modrinth.get("server_side") or "").lower()
            if server_side == "unsupported":
                diagnostics.append(
                    AddonDiagnostic(
                        severity="BLOCKER",
                        category="client_only_mod",
                        message=f"{asset.name or asset.file_name} 的 Modrinth 项目标记为不支持服务端。",
                        evidence_type="api_hash_match",
                        confidence="api_hash_match",
                        affected_files=[asset.relative_path],
                        evidence={"provider": "modrinth", "project": modrinth},
                        sources=[_source("Modrinth", f"https://modrinth.com/mod/{modrinth.get('slug')}" if modrinth.get("slug") else "https://modrinth.com")],
                    )
                )
            game_versions = [str(item) for item in modrinth.get("game_versions", [])]
            if server_version and game_versions and server_version not in game_versions:
                diagnostics.append(
                    AddonDiagnostic(
                        severity="BLOCKER",
                        category="minecraft_version_mismatch",
                        message=f"{asset.name or asset.file_name} 的 Modrinth 匹配版本不支持 Minecraft {server_version}。",
                        evidence_type="api_hash_match",
                        confidence="api_hash_match",
                        affected_files=[asset.relative_path],
                        evidence={"provider": "modrinth", "server_version": server_version, "game_versions": game_versions},
                        sources=[_source("Modrinth", f"https://modrinth.com/mod/{modrinth.get('slug')}" if modrinth.get("slug") else "https://modrinth.com")],
                    )
                )
            for dependency in modrinth.get("dependencies", []):
                if not isinstance(dependency, dict):
                    continue
                dep_type = str(dependency.get("dependency_type") or "").lower()
                project_id = dependency.get("project_id")
                if dep_type != "required" or not project_id or str(project_id) in modrinth_project_ids:
                    continue
                diagnostics.append(
                    AddonDiagnostic(
                        severity="BLOCKER",
                        category="missing_dependency",
                        message=f"{asset.name or asset.file_name} 缺少 Modrinth 标记的必需依赖。",
                        evidence_type="api_hash_match",
                        confidence="api_hash_match",
                        affected_files=[asset.relative_path],
                        evidence={"provider": "modrinth", "dependency": dependency},
                        sources=[_source("Modrinth", f"https://modrinth.com/mod/{modrinth.get('slug')}" if modrinth.get("slug") else "https://modrinth.com")],
                    )
                )
        curseforge = asset.knowledge.get("curseforge", {})
        if isinstance(curseforge, dict) and server_version:
            versions = [str(item) for item in curseforge.get("game_versions", [])]
            minecraft_versions = [item for item in versions if item.startswith("1.")]
            if minecraft_versions and server_version not in minecraft_versions:
                diagnostics.append(
                    AddonDiagnostic(
                        severity="BLOCKER",
                        category="minecraft_version_mismatch",
                        message=f"{asset.name or asset.file_name} 的 CurseForge 匹配文件不支持 Minecraft {server_version}。",
                        evidence_type="api_hash_match",
                        confidence="api_hash_match",
                        affected_files=[asset.relative_path],
                        evidence={"provider": "curseforge", "server_version": server_version, "game_versions": minecraft_versions},
                        sources=[_source("CurseForge", "https://www.curseforge.com/minecraft")],
                    )
                )
    return diagnostics


def _version_from_server_jar(server_jar: Path) -> str | None:
    text = server_jar.name
    match = __import__("re").search(r"(?<!\d)(1\.\d+(?:\.\d+)?)(?!\d)", text)
    return match.group(1) if match else None


def _curseforge_fingerprint(path: Path) -> int | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return _murmur2(data)


def _murmur2(data: bytes) -> int:
    length = len(data)
    seed = 1
    m = 0x5BD1E995
    r = 24
    h = (seed ^ length) & 0xFFFFFFFF
    rounded_end = length & 0xFFFFFFFC
    for i in range(0, rounded_end, 4):
        k = data[i] | (data[i + 1] << 8) | (data[i + 2] << 16) | (data[i + 3] << 24)
        k = (k * m) & 0xFFFFFFFF
        k ^= (k & 0xFFFFFFFF) >> r
        k = (k * m) & 0xFFFFFFFF
        h = (h * m) & 0xFFFFFFFF
        h ^= k
    remaining = length & 3
    if remaining == 3:
        h ^= data[rounded_end + 2] << 16
    if remaining >= 2:
        h ^= data[rounded_end + 1] << 8
    if remaining >= 1:
        h ^= data[rounded_end]
        h = (h * m) & 0xFFFFFFFF
    h ^= (h & 0xFFFFFFFF) >> 13
    h = (h * m) & 0xFFFFFFFF
    h ^= (h & 0xFFFFFFFF) >> 15
    return h & 0xFFFFFFFF


def _expires_at(ttl_hours: int) -> str:
    ttl = max(1, int(ttl_hours or 72))
    return (datetime.now(timezone.utc) + timedelta(hours=ttl)).isoformat()


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _short_hash(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _source(label: str, url: str) -> dict[str, str]:
    return {"label": label, "url": url}
