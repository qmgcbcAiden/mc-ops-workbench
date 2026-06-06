from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.mc.config_files import (
    get_config_spec,
    list_config_files,
    normalize_config_key,
)
from src.mc.config_policy import BLOCKED, HIGH, LOW, max_risk, validate_config_value
from src.mc.config_secret_redactor import redact_properties
from src.mc.properties_parser import parse_properties
from src.mc.server_files import content_hash
from src.repositories.config_change_repository import ConfigChangeRepository
from src.service.file_service import FileService


_RISK_ORDER: dict[str, int] = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "BLOCKED": 3}


class ConfigEditService:
    def __init__(
        self,
        file_service: FileService,
        change_repo: ConfigChangeRepository,
        version_service: Any | None = None,
        source_dir: str | Path | None = None,
        auto_approve_max_risk: str = "NONE",
        redaction_version: str = "v1",
    ) -> None:
        self._file_service = file_service
        self._repo = change_repo
        self._version_service = version_service
        self._source_dir = Path(source_dir) if source_dir else None
        self._auto_approve_max_risk = (auto_approve_max_risk or "NONE").upper()
        self._redaction_version = redaction_version

    def list_capabilities(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "files": list_config_files(),
        }

    def read_config_file(self, relative_path: str) -> dict[str, Any]:
        spec = get_config_spec(relative_path)
        if not spec:
            return _rejected(relative_path, "该配置文件不在 Agent 可读取白名单内。")
        relative_path = spec["relative_path"]

        read_result = self._read_config_text(relative_path)
        if read_result["status"] != "ok":
            return read_result

        content = read_result["content"]
        doc = parse_properties(content)
        allowed_keys = spec.get("keys", {})
        entries = []
        visible_lines = []
        hidden_entry_count = 0
        seen_allowed: set[str] = set()

        for line in doc.lines:
            if line.kind != "entry" or line.key is None:
                continue
            if line.key not in allowed_keys:
                hidden_entry_count += 1
                continue
            seen_allowed.add(line.key)
            visible_lines.append(f"{line.key}={line.value or ''}")

        for key, key_spec in allowed_keys.items():
            value = doc.get(key)
            entries.append({
                "key": key,
                "value": value,
                "exists": value is not None,
                "duplicate_count": doc.entry_count(key),
                "metadata": _public_key_metadata(key_spec),
            })

        return {
            "status": "ok",
            "relative_path": relative_path,
            "format": spec.get("format"),
            "description": spec.get("description"),
            "before_hash": content_hash(content),
            "entries": entries,
            "visible_content": "\n".join(visible_lines),
            "visible_key_count": len(seen_allowed),
            "hidden_entry_count": hidden_entry_count,
            "message": "已读取白名单配置项；未返回非白名单或敏感配置项。",
        }

    def get_values(self, relative_path: str, keys: list[str]) -> dict[str, Any]:
        spec = get_config_spec(relative_path)
        if not spec:
            return _rejected(relative_path, "该配置文件不在 Agent 可修改白名单内。")
        relative_path = spec["relative_path"]

        read_result = self._read_config_text(relative_path)
        if read_result["status"] != "ok":
            return read_result

        doc = parse_properties(read_result["content"])
        values = []
        for key_or_alias in keys:
            key = normalize_config_key(relative_path, key_or_alias)
            if not key:
                values.append({
                    "key": key_or_alias,
                    "allowed": False,
                    "error_message": "该配置项不在白名单内。",
                })
                continue
            values.append({
                "key": key,
                "allowed": True,
                "value": doc.get(key),
                "exists": doc.get(key) is not None,
                "duplicate_count": doc.entry_count(key),
                "metadata": _public_key_metadata(spec["keys"][key]),
            })

        return {
            "status": "ok",
            "relative_path": relative_path,
            "values": values,
            "before_hash": content_hash(read_result["content"]),
        }

    def propose_change(
        self,
        relative_path: str,
        changes: list[dict[str, Any]],
        user_request: str,
        session_id: str | None = None,
        allow_auto_apply: bool = True,
    ) -> dict[str, Any]:
        spec = get_config_spec(relative_path)
        if not spec:
            return _rejected(relative_path, "该配置文件不在 Agent 可修改白名单内。")
        relative_path = spec["relative_path"]
        if not changes:
            return _rejected(relative_path, "没有可生成草案的配置项。")

        read_result = self._read_config_text(relative_path)
        if read_result["status"] != "ok":
            return read_result

        before_content = read_result["content"]
        before_hash = content_hash(before_content)
        doc = parse_properties(before_content)

        normalized_changes: list[dict[str, Any]] = []
        warnings: list[str] = []
        risk_level = LOW
        restart_required = False

        for change in changes:
            raw_key = str(change.get("key", ""))
            key = normalize_config_key(relative_path, raw_key)
            if not key:
                return _rejected(relative_path, f"配置项 {raw_key or '(空)'} 不在白名单内。")

            key_spec = spec["keys"][key]
            value_result = validate_config_value(key, change.get("value", ""), key_spec)
            if not value_result.valid or value_result.risk_level == BLOCKED:
                return _rejected(relative_path, value_result.error_message or f"{key} 的目标值不合法。")

            duplicate_count = doc.entry_count(key)
            if duplicate_count > 1:
                risk_level = max_risk(risk_level, "MEDIUM")
                warnings.append(f"{key} 在文件中出现 {duplicate_count} 次，将只修改最后一个有效项。")

            old_value = doc.get(key)
            new_value = value_result.normalized_value or ""
            doc.set(key, new_value)
            risk_level = max_risk(risk_level, value_result.risk_level)
            restart_required = restart_required or value_result.restart_required
            warnings.extend(value_result.warnings)
            normalized_changes.append({
                "key": key,
                "old_value": old_value,
                "new_value": new_value,
                "risk_level": value_result.risk_level,
                "restart_required": value_result.restart_required,
                "reason": str(change.get("reason", "")).strip(),
            })

        after_content = doc.to_text()
        formatting = self._file_service.format_text_file(relative_path, after_content)
        if formatting.get("status") == "failed":
            return _rejected(relative_path, formatting.get("error_message") or "配置格式化失败。")
        after_content = formatting.get("content", after_content)
        after_hash = content_hash(after_content)
        if before_hash == after_hash:
            return {
                "status": "no_change",
                "relative_path": relative_path,
                "message": "目标配置值与当前文件一致，未生成草案。",
            }

        validation = self._file_service.validate_text_file(relative_path, after_content)
        if not validation.get("valid"):
            return _rejected(relative_path, validation.get("error") or "配置文件格式校验失败。")

        diff_text = _unified_diff(
            relative_path,
            redact_properties(before_content),
            redact_properties(after_content),
        )
        proposal_id = f"cfgp_{uuid4().hex[:16]}"
        warnings = _unique(warnings)
        self._repo.create_proposal(
            proposal_id=proposal_id,
            session_id=session_id,
            user_request=user_request,
            relative_path=relative_path,
            before_hash=before_hash,
            after_hash=after_hash,
            before_content=redact_properties(before_content),
            after_content=redact_properties(after_content),
            diff_text=diff_text,
            changes=normalized_changes,
            risk_level=risk_level,
            restart_required=restart_required,
            warnings=warnings,
        )

        if allow_auto_apply and self._should_auto_approve(risk_level):
            return self._auto_apply(
                proposal_id=proposal_id,
                relative_path=relative_path,
                after_content=after_content,
                risk_level=risk_level,
                restart_required=restart_required,
                normalized_changes=normalized_changes,
                warnings=warnings,
                diff_text=diff_text,
            )

        return {
            "status": "proposal_created",
            "proposal_id": proposal_id,
            "relative_path": relative_path,
            "risk_level": risk_level,
            "confirmation_required": risk_level == HIGH,
            "restart_required": restart_required,
            "changes": normalized_changes,
            "warnings": warnings,
            "diff": diff_text,
            "preview_content": redact_properties(after_content),
            "before_hash": before_hash,
            "after_hash": after_hash,
            "message": "已生成配置修改草案，等待用户在配置编辑器中采纳或拒绝。",
        }

    def get_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        proposal = self._repo.get(proposal_id)
        if not proposal:
            return None
        return _proposal_public(proposal)

    def list_recent_proposals(
        self,
        limit: int = 50,
        relative_path: str | None = None,
    ) -> list[dict[str, Any]]:
        proposals = self._repo.list_recent(limit=limit)
        if relative_path:
            proposals = [
                proposal for proposal in proposals
                if proposal.get("relative_path") == relative_path
            ]
        return [_proposal_public(proposal) for proposal in proposals]

    def reject_proposal(
        self,
        proposal_id: str,
        confirmed_by: str = "local_user",
    ) -> dict[str, Any]:
        proposal = self._repo.get(proposal_id)
        if not proposal:
            return {
                "status": "not_found",
                "proposal_id": proposal_id,
                "message": "未找到配置修改草案。",
            }
        if proposal["status"] != "pending":
            return {
                "status": "invalid_status",
                "proposal_id": proposal_id,
                "message": f"草案状态为 {proposal['status']}，不能拒绝。",
            }
        self._repo.update_status(
            proposal_id,
            "rejected",
            confirmed_by=confirmed_by,
            set_confirmed_at=True,
        )
        return {
            "status": "rejected",
            "proposal_id": proposal_id,
            "relative_path": proposal["relative_path"],
            "message": "配置修改草案已拒绝，未写入文件。",
        }

    def apply_proposal(
        self,
        proposal_id: str,
        confirmed_by: str = "local_user",
        high_risk_confirmed: bool = False,
    ) -> dict[str, Any]:
        proposal = self._repo.get(proposal_id)
        if not proposal:
            return {
                "status": "not_found",
                "proposal_id": proposal_id,
                "message": "未找到配置修改草案。",
            }

        if proposal["status"] not in {"pending", "confirmed"}:
            return {
                "status": "invalid_status",
                "proposal_id": proposal_id,
                "message": f"草案状态为 {proposal['status']}，不能应用。",
            }

        if proposal["risk_level"] == HIGH and not high_risk_confirmed:
            return {
                "status": "confirmation_required",
                "proposal_id": proposal_id,
                "risk_level": HIGH,
                "message": "高风险配置修改需要明确确认后才能应用。",
            }

        read_result = self._read_config_text(proposal["relative_path"])
        if read_result["status"] != "ok":
            self._repo.update_status(
                proposal_id,
                "failed",
                error_message=read_result.get("message"),
            )
            return read_result

        current_hash = content_hash(read_result["content"])
        if current_hash != proposal["before_hash"]:
            message = f"{proposal['relative_path']} 在草案生成后发生变化，请重新生成修改方案。"
            self._repo.update_status(proposal_id, "failed", error_message=message)
            return {
                "status": "conflict",
                "proposal_id": proposal_id,
                "relative_path": proposal["relative_path"],
                "message": message,
            }

        target_content = _apply_changes_to_content(
            proposal["relative_path"],
            read_result["content"],
            proposal["changes"],
        )
        formatting = self._file_service.format_text_file(proposal["relative_path"], target_content)
        if formatting.get("status") == "failed":
            message = formatting.get("error_message") or "配置格式化失败。"
            self._repo.update_status(proposal_id, "failed", error_message=message)
            return {
                "status": "failed",
                "proposal_id": proposal_id,
                "relative_path": proposal["relative_path"],
                "message": message,
            }
        target_content = formatting.get("content", target_content)
        validation_error = self._validate_after_content(proposal, target_content)
        if validation_error:
            self._repo.update_status(proposal_id, "failed", error_message=validation_error)
            return {
                "status": "failed",
                "proposal_id": proposal_id,
                "relative_path": proposal["relative_path"],
                "message": validation_error,
            }

        baseline_result = self._prepare_version_baseline(proposal["relative_path"])
        if baseline_result.get("status") not in {"skipped", "current", "committed"}:
            message = baseline_result.get("error") or "无法建立配置版本基线，已取消保存。"
            self._repo.update_status(proposal_id, "failed", error_message=message)
            return {
                "status": "failed",
                "proposal_id": proposal_id,
                "relative_path": proposal["relative_path"],
                "message": message,
            }

        save_result = self._file_service.save_text_file(
            proposal["relative_path"],
            target_content,
            create_backup=not self._uses_git_versioning(),
            track_version=False,
        )
        if save_result.get("status") != "saved":
            message = save_result.get("error_message") or "保存配置文件失败。"
            self._repo.update_status(proposal_id, "failed", error_message=message)
            return {
                "status": "failed",
                "proposal_id": proposal_id,
                "relative_path": proposal["relative_path"],
                "message": message,
            }

        self._repo.update_status(
            proposal_id,
            "applied",
            confirmed_by=confirmed_by,
            backup_path=save_result.get("backup_path"),
            set_confirmed_at=True,
            set_applied_at=True,
        )
        version_result = self._try_snapshot(
            proposal_id=proposal_id,
            relative_path=proposal["relative_path"],
            actor="user",
            risk_level=proposal["risk_level"],
            auto_approved=False,
            message=f"config({proposal['relative_path']}): {_changes_summary(proposal.get('changes', []))}",
        )
        return {
            "status": "saved",
            "proposal_id": proposal_id,
            "relative_path": proposal["relative_path"],
            "backup_path": save_result.get("backup_path"),
            "restart_required": proposal["restart_required"],
            "version_status": version_result.get("status"),
            "version_commit_id": version_result.get("commit_id"),
            "message": "配置已保存，通常需要重启服务器后生效。" if proposal["restart_required"] else "配置已保存。",
        }

    def rollback_change(
        self,
        proposal_id: str,
        confirmed_by: str = "local_user",
    ) -> dict[str, Any]:
        proposal = self._repo.get(proposal_id)
        if not proposal:
            return {"status": "not_found", "proposal_id": proposal_id, "message": "未找到配置修改记录。"}
        if proposal["status"] != "applied":
            return {"status": "invalid_status", "proposal_id": proposal_id, "message": "只有已应用的草案可以回滚。"}

        read_result = self._read_config_text(proposal["relative_path"])
        if read_result["status"] != "ok":
            return read_result
        if content_hash(read_result["content"]) != proposal["after_hash"]:
            return {
                "status": "conflict",
                "proposal_id": proposal_id,
                "message": "当前文件已不是该草案应用后的内容，不能自动回滚。",
            }

        rollback_content = _rollback_changes_in_content(
            proposal["relative_path"],
            read_result["content"],
            proposal["changes"],
        )
        formatting = self._file_service.format_text_file(proposal["relative_path"], rollback_content)
        if formatting.get("status") == "failed":
            return {
                "status": "failed",
                "proposal_id": proposal_id,
                "message": formatting.get("error_message") or "回滚配置格式化失败。",
            }
        rollback_content = formatting.get("content", rollback_content)
        validation = self._file_service.validate_text_file(
            proposal["relative_path"],
            rollback_content,
        )
        if not validation.get("valid"):
            return {
                "status": "failed",
                "proposal_id": proposal_id,
                "message": validation.get("error") or "回滚后的配置文件格式校验失败。",
            }

        baseline_result = self._prepare_version_baseline(proposal["relative_path"])
        if baseline_result.get("status") not in {"skipped", "current", "committed"}:
            return {
                "status": "failed",
                "proposal_id": proposal_id,
                "message": baseline_result.get("error") or "无法建立配置版本基线，已取消回滚。",
            }

        save_result = self._file_service.save_text_file(
            proposal["relative_path"],
            rollback_content,
            create_backup=not self._uses_git_versioning(),
            track_version=False,
        )
        if save_result.get("status") != "saved":
            return {
                "status": "failed",
                "proposal_id": proposal_id,
                "message": save_result.get("error_message") or "回滚保存失败。",
            }
        self._repo.update_status(
            proposal_id,
            "rolled_back",
            confirmed_by=confirmed_by,
            backup_path=save_result.get("backup_path"),
            set_applied_at=True,
        )
        version_result = self._try_snapshot(
            proposal_id=proposal_id,
            relative_path=proposal["relative_path"],
            actor="user",
            risk_level=proposal["risk_level"],
            auto_approved=False,
            message=f"rollback config({proposal['relative_path']}): revert {_changes_summary(proposal.get('changes', []))}",
        )
        return {
            "status": "rolled_back",
            "proposal_id": proposal_id,
            "relative_path": proposal["relative_path"],
            "backup_path": save_result.get("backup_path"),
            "version_status": version_result.get("status"),
            "version_commit_id": version_result.get("commit_id"),
            "message": "配置已回滚到草案生成前的内容。",
        }

    def _read_config_text(self, relative_path: str) -> dict[str, Any]:
        try:
            preview = self._file_service.preview_file(relative_path)
        except Exception as exc:
            return _rejected(relative_path, f"配置文件读取失败：{exc}")
        if not preview.get("previewable", True):
            return _rejected(relative_path, preview.get("content") or "该配置文件不可读取。")
        if preview.get("truncated"):
            return _rejected(relative_path, "配置文件超过预览大小限制，拒绝交给 Agent 修改。")
        return {
            "status": "ok",
            "relative_path": relative_path,
            "content": preview.get("content", ""),
        }

    def _validate_after_content(self, proposal: dict[str, Any], after_content: str) -> str | None:
        validation = self._file_service.validate_text_file(
            proposal["relative_path"],
            after_content,
        )
        if not validation.get("valid"):
            return validation.get("error") or "配置文件格式校验失败。"

        spec = get_config_spec(proposal["relative_path"])
        if not spec:
            return "该配置文件不在 Agent 可修改白名单内。"
        doc = parse_properties(after_content)
        for change in proposal["changes"]:
            key = change.get("key")
            if key not in spec["keys"]:
                return f"配置项 {key} 不在白名单内。"
            expected = str(change.get("new_value", ""))
            if doc.get(key) != expected:
                return f"配置项 {key} 的目标值与草案不一致。"
            value_result = validate_config_value(key, expected, spec["keys"][key])
            if not value_result.valid:
                return value_result.error_message or f"配置项 {key} 的目标值不合法。"
        return None

    def _should_auto_approve(self, risk_level: str) -> bool:
        if self._auto_approve_max_risk in {"", "NONE", "DISABLED", "FALSE"}:
            return False
        if risk_level == BLOCKED:
            return False
        max_risk_value = _RISK_ORDER.get(self._auto_approve_max_risk)
        if max_risk_value is None:
            return False
        return _RISK_ORDER.get(risk_level, 99) <= max_risk_value

    def _auto_apply(
        self,
        proposal_id: str,
        relative_path: str,
        after_content: str,
        risk_level: str,
        restart_required: bool,
        normalized_changes: list[dict[str, Any]],
        warnings: list[str],
        diff_text: str,
    ) -> dict[str, Any]:
        baseline_result = self._prepare_version_baseline(relative_path)
        if baseline_result.get("status") not in {"skipped", "current", "committed"}:
            message = baseline_result.get("error") or "无法建立配置版本基线，已取消自动保存。"
            self._repo.update_status(proposal_id, "failed", error_message=message)
            return {
                "status": "failed",
                "proposal_id": proposal_id,
                "relative_path": relative_path,
                "message": message,
            }

        save_result = self._file_service.save_text_file(
            relative_path,
            after_content,
            create_backup=not self._uses_git_versioning(),
            track_version=False,
        )
        if save_result.get("status") != "saved":
            message = save_result.get("error_message") or "自动保存配置文件失败。"
            self._repo.update_status(proposal_id, "failed", error_message=message)
            return {
                "status": "failed",
                "proposal_id": proposal_id,
                "relative_path": relative_path,
                "message": message,
            }

        self._repo.update_status(
            proposal_id,
            "applied",
            confirmed_by="auto_approval",
            backup_path=save_result.get("backup_path"),
            set_confirmed_at=True,
            set_applied_at=True,
        )
        self._repo.update_auto_approval(
            proposal_id=proposal_id,
            auto_approved=True,
            approval_policy=f"auto_{self._auto_approve_max_risk.lower()}",
            redaction_version=self._redaction_version,
        )
        version_result = self._try_snapshot(
            proposal_id=proposal_id,
            relative_path=relative_path,
            actor="agent",
            risk_level=risk_level,
            auto_approved=True,
            message=f"auto config({relative_path}): {_changes_summary(normalized_changes)}",
        )

        return {
            "status": "saved",
            "auto_approved": True,
            "proposal_id": proposal_id,
            "relative_path": relative_path,
            "risk_level": risk_level,
            "restart_required": restart_required,
            "changes": normalized_changes,
            "warnings": warnings,
            "version_status": version_result.get("status"),
            "version_commit_id": version_result.get("commit_id"),
            "diff": diff_text,
            "message": "配置已自动保存。" + (" 通常需要重启服务器后生效。" if restart_required else ""),
        }

    def _try_snapshot(
        self,
        proposal_id: str,
        relative_path: str,
        actor: str,
        risk_level: str,
        auto_approved: bool,
        message: str,
    ) -> dict[str, Any]:
        if not self._version_service or not self._source_dir:
            return {"status": "skipped"}
        try:
            source_path = self._source_dir / relative_path
            result = self._version_service.snapshot_after_change(
                source_path=source_path,
                relative_path=relative_path,
                proposal_id=proposal_id,
                actor=actor,
                risk_level=risk_level,
                auto_approved=auto_approved,
                message=message,
            )
        except Exception as exc:
            result = {"status": "failed", "error": str(exc)}

        try:
            self._repo.update_version_status(
                proposal_id=proposal_id,
                version_status=result.get("status", "failed"),
                version_commit_id=result.get("commit_id"),
                version_error_message=result.get("error"),
                redaction_version=self._redaction_version,
            )
        except Exception:
            pass
        return result

    def _uses_git_versioning(self) -> bool:
        return self._version_service is not None and self._source_dir is not None

    def _prepare_version_baseline(self, relative_path: str) -> dict[str, Any]:
        if not self._uses_git_versioning():
            return {"status": "skipped"}
        try:
            return self._version_service.ensure_baseline_snapshot(
                source_path=self._source_dir / relative_path,
                relative_path=relative_path,
            )
        except Exception as exc:
            return {"status": "failed", "error": str(exc)}


def _unified_diff(relative_path: str, before: str, after: str) -> str:
    return "\n".join(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=relative_path,
            tofile=relative_path,
            lineterm="",
        )
    )


def _apply_changes_to_content(
    relative_path: str,
    content: str,
    changes: list[dict[str, Any]],
) -> str:
    spec = get_config_spec(relative_path)
    if not spec:
        return content
    doc = parse_properties(content)
    for change in changes:
        key = str(change.get("key", ""))
        if key not in spec["keys"]:
            continue
        doc.set(key, str(change.get("new_value", "")))
    return doc.to_text()


def _rollback_changes_in_content(
    relative_path: str,
    content: str,
    changes: list[dict[str, Any]],
) -> str:
    spec = get_config_spec(relative_path)
    if not spec:
        return content
    doc = parse_properties(content)
    for change in changes:
        key = str(change.get("key", ""))
        if key not in spec["keys"]:
            continue
        old_value = change.get("old_value")
        if old_value is None:
            _remove_last_property(doc, key)
        else:
            doc.set(key, str(old_value))
    return doc.to_text()


def _remove_last_property(doc: Any, key: str) -> None:
    for index in range(len(doc.lines) - 1, -1, -1):
        line = doc.lines[index]
        if line.kind == "entry" and line.key == key:
            del doc.lines[index]
            _remove_added_comment_before(doc, index, key)
            return


def _remove_added_comment_before(doc: Any, index: int, key: str) -> None:
    comment_index = index - 1
    if comment_index < 0:
        return
    comment = doc.lines[comment_index]
    if comment.kind == "comment" and comment.raw == f"# Added by MC ops dashboard: {key}":
        del doc.lines[comment_index]
        blank_index = comment_index - 1
        if blank_index >= 0 and doc.lines[blank_index].kind == "blank":
            del doc.lines[blank_index]


def _public_key_metadata(key_spec: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in key_spec.items()
        if key != "value_aliases"
    }


def _proposal_public(proposal: dict[str, Any]) -> dict[str, Any]:
    return {
        "proposal_id": proposal["id"],
        "session_id": proposal.get("session_id"),
        "user_request": proposal.get("user_request"),
        "relative_path": proposal["relative_path"],
        "diff": proposal["diff_text"],
        "preview_content": proposal["after_content"],
        "changes": proposal["changes"],
        "risk_level": proposal["risk_level"],
        "confirmation_required": proposal["risk_level"] == HIGH,
        "restart_required": proposal["restart_required"],
        "warnings": proposal["warnings"],
        "status": proposal["status"],
        "backup_path": proposal.get("backup_path"),
        "error_message": proposal.get("error_message"),
        "auto_approved": bool(proposal.get("auto_approved")),
        "approval_policy": proposal.get("approval_policy"),
        "version_commit_id": proposal.get("version_commit_id"),
        "version_status": proposal.get("version_status"),
        "version_error_message": proposal.get("version_error_message"),
        "redaction_version": proposal.get("redaction_version"),
    }


def _rejected(relative_path: str, message: str) -> dict[str, Any]:
    return {
        "status": "rejected",
        "relative_path": relative_path,
        "message": message,
    }


def _unique(values: list[str]) -> list[str]:
    seen = set()
    unique = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


def _changes_summary(changes: list[dict[str, Any]]) -> str:
    parts = []
    for change in changes:
        key = change.get("key", "?")
        old = change.get("old_value", "?")
        new = change.get("new_value", "?")
        parts.append(f"{key} {old} -> {new}")
    return ", ".join(parts) if parts else "update"
