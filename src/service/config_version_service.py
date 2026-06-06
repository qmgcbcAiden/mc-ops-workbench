from __future__ import annotations

from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

from dulwich import porcelain
from dulwich.repo import Repo

from src.mc.config_secret_redactor import redact_config_text
from src.mc.server_files import content_hash
from src.repositories.config_version_repository import ConfigVersionRepository


class ConfigVersionService:
    def __init__(
        self,
        repo_dir: str | Path,
        version_repo: ConfigVersionRepository,
    ) -> None:
        self._repo_path = Path(repo_dir)
        self._version_repo = version_repo

    def ensure_repo(self) -> dict[str, Any]:
        if self._repo_path.exists():
            try:
                Repo(str(self._repo_path))
                return {"status": "ok", "repo_path": str(self._repo_path), "created": False}
            except Exception:
                pass
        self._repo_path.mkdir(parents=True, exist_ok=True)
        Repo.init(str(self._repo_path))
        return {"status": "ok", "repo_path": str(self._repo_path), "created": True}

    def snapshot_file(
        self,
        source_path: str | Path,
        relative_path: str,
        actor: str = "system",
        message: str | None = None,
        source: str = "system",
    ) -> dict[str, Any]:
        normalized_path = _safe_relative_path(relative_path)
        if not normalized_path:
            return {"status": "rejected", "relative_path": relative_path, "error": "Invalid relative path."}
        relative_path = normalized_path
        source_file = Path(source_path)
        if not source_file.is_file():
            return {"status": "missing", "relative_path": relative_path}

        repo_result = self.ensure_repo()
        if repo_result["status"] != "ok":
            return {"status": "repo_error", "error": str(repo_result)}

        try:
            raw = source_file.read_text(encoding="utf-8")
        except OSError as exc:
            return {"status": "read_error", "error": str(exc)}

        redacted = self._redact_content(relative_path, raw)
        if not message:
            message = f"config snapshot: {relative_path}"

        commit_id = self._commit_file(relative_path, redacted, message)
        parent_id = self._get_parent_commit_id(relative_path)

        self._version_repo.create_commit(
            commit_id=commit_id,
            parent_commit_id=parent_id,
            proposal_id=None,
            relative_path=relative_path,
            actor=actor,
            source=source,
            message=message,
            risk_level=None,
            auto_approved=True,
            status="committed",
        )

        return {
            "status": "committed",
            "commit_id": commit_id,
            "relative_path": relative_path,
        }

    def ensure_baseline_snapshot(
        self,
        source_path: str | Path,
        relative_path: str,
    ) -> dict[str, Any]:
        normalized_path = _safe_relative_path(relative_path)
        if not normalized_path:
            return {"status": "rejected", "relative_path": relative_path, "error": "Invalid relative path."}

        source = Path(source_path)
        if not source.is_file():
            return {"status": "missing", "relative_path": normalized_path}
        repo_result = self.ensure_repo()
        if repo_result["status"] != "ok":
            return {"status": "repo_error", "error": str(repo_result)}

        try:
            current_content = self._redact_content(
                normalized_path,
                source.read_text(encoding="utf-8"),
            )
        except OSError as exc:
            return {"status": "read_error", "error": str(exc)}

        history = self._version_repo.list_for_path(normalized_path, limit=1)
        tracked_file = self._repo_path / normalized_path
        if (
            history
            and tracked_file.is_file()
            and tracked_file.read_text(encoding="utf-8") == current_content
        ):
            return {
                "status": "current",
                "commit_id": history[0]["commit_id"],
                "relative_path": normalized_path,
            }

        return self.snapshot_file(
            source_path=source,
            relative_path=normalized_path,
            actor="system",
            message=f"baseline config({normalized_path})",
        )

    def snapshot_after_change(
        self,
        source_path: str | Path,
        relative_path: str,
        proposal_id: str,
        actor: str,
        risk_level: str,
        auto_approved: bool,
        message: str,
    ) -> dict[str, Any]:
        normalized_path = _safe_relative_path(relative_path)
        if not normalized_path:
            return {"status": "rejected", "relative_path": relative_path, "error": "Invalid relative path."}
        relative_path = normalized_path
        source = Path(source_path)
        if not source.is_file():
            return {"status": "missing", "relative_path": relative_path}

        repo_result = self.ensure_repo()
        if repo_result["status"] != "ok":
            return {"status": "repo_error", "error": str(repo_result)}

        try:
            raw = source.read_text(encoding="utf-8")
        except OSError as exc:
            return {"status": "read_error", "error": str(exc)}

        redacted = self._redact_content(relative_path, raw)
        commit_id = self._commit_file(relative_path, redacted, message)
        parent_id = self._get_parent_commit_id(relative_path)

        self._version_repo.create_commit(
            commit_id=commit_id,
            parent_commit_id=parent_id,
            proposal_id=proposal_id,
            relative_path=relative_path,
            actor=actor,
            source="agent" if actor == "agent" else "user",
            message=message,
            risk_level=risk_level,
            auto_approved=auto_approved,
            status="committed",
        )

        self._version_repo.update_proposal_version(
            proposal_id=proposal_id,
            commit_id=commit_id,
            status="committed",
        )

        return {
            "status": "committed",
            "commit_id": commit_id,
            "relative_path": relative_path,
        }

    def list_history(self, relative_path: str, limit: int = 50) -> list[dict[str, Any]]:
        relative_path = _safe_relative_path(relative_path) or relative_path
        return self._version_repo.list_for_path(relative_path, limit=limit)

    def diff_commit(self, commit_id: str) -> dict[str, Any]:
        record = self._version_repo.get_by_commit_id(commit_id)
        if not record:
            return {"status": "not_found", "commit_id": commit_id}

        try:
            repo = Repo(str(self._repo_path))
        except Exception as exc:
            return {"status": "repo_error", "error": str(exc)}

        commit_sha = record["commit_id"].encode("utf-8")
        parent_sha = None
        if record.get("parent_commit_id"):
            parent_sha = record["parent_commit_id"].encode("utf-8")

        if parent_sha:
            try:
                diff_text = self._unified_diff_bytes(repo, parent_sha, commit_sha)
            except Exception:
                diff_text = ""
        else:
            diff_text = self._initial_commit_content(repo, commit_sha)

        return {
            "status": "ok",
            "commit_id": commit_id,
            "parent_commit_id": record.get("parent_commit_id"),
            "relative_path": record["relative_path"],
            "message": record["message"],
            "created_at": record["created_at"],
            "diff_text": diff_text,
        }

    def get_file_at_commit(self, commit_id: str) -> dict[str, Any]:
        """Return the file content at a specific commit (redacted)."""
        record = self._version_repo.get_by_commit_id(commit_id)
        if not record:
            return {"status": "not_found", "commit_id": commit_id}

        try:
            repo = Repo(str(self._repo_path))
        except Exception as exc:
            return {"status": "repo_error", "error": str(exc)}

        commit_sha = commit_id.encode("utf-8")
        relative_path = record["relative_path"]

        try:
            content = self._cat_file(repo, commit_sha, relative_path)
        except Exception as exc:
            return {"status": "read_error", "error": str(exc)}

        return {
            "status": "ok",
            "commit_id": commit_id,
            "relative_path": relative_path,
            "content": content,
        }

    # --- private helpers ---

    def _redact_content(self, relative_path: str, raw: str) -> str:
        return redact_config_text(relative_path, raw)

    def _commit_file(self, relative_path: str, content: str, message: str) -> str:
        repo = Repo(str(self._repo_path))

        target = self._repo_path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_text(encoding="utf-8") == content:
            try:
                return repo.head().decode("utf-8")
            except Exception:
                pass
        target.write_text(content, encoding="utf-8")

        porcelain.add(repo, [relative_path])
        commit_sha = porcelain.commit(
            repo,
            message=message.encode("utf-8"),
            committer=b"mc-ops-dashboard <dashboard@localhost>",
            author=b"mc-ops-dashboard <dashboard@localhost>",
        )
        return commit_sha.decode("utf-8")

    def _get_parent_commit_id(self, relative_path: str) -> str | None:
        records = self._version_repo.list_for_path(relative_path, limit=1)
        if records:
            return records[0]["commit_id"]
        return None

    def _unified_diff_bytes(self, repo: Repo, old_sha: bytes, new_sha: bytes) -> str:
        """Generate unified diff between two commits for all files."""
        from io import BytesIO

        buf = BytesIO()
        porcelain.diff(repo, old_sha, new_sha, outstream=buf)
        return buf.getvalue().decode("utf-8", errors="replace")

    def _initial_commit_content(self, repo: Repo, commit_sha: bytes) -> str:
        """Return the full file content as a pseudo-diff for the initial commit."""
        try:
            tree = repo[repo[commit_sha].tree]
            # Walk the tree to get file content
            parts: list[str] = []
            for entry in tree.items():
                path = entry.path.decode("utf-8") if isinstance(entry.path, bytes) else entry.path
                blob = repo[entry.sha]
                content = blob.data.decode("utf-8", errors="replace")
                parts.append(f"+++ {path}\n{content}")
            return "\n".join(parts)
        except Exception:
            return ""

    def _cat_file(self, repo: Repo, commit_sha: bytes, path: str) -> str:
        """Return the content of a file at a specific commit."""
        tree = repo[repo[commit_sha].tree]
        for entry in tree.items():
            entry_path = entry.path.decode("utf-8") if isinstance(entry.path, bytes) else entry.path
            if entry_path == path:
                blob = repo[entry.sha]
                return blob.data.decode("utf-8", errors="replace")
        return ""


def init_version_service(repo_dir: str | Path, version_repo: ConfigVersionRepository) -> ConfigVersionService:
    service = ConfigVersionService(repo_dir=repo_dir, version_repo=version_repo)
    service.ensure_repo()
    return service


def _safe_relative_path(relative_path: str) -> str | None:
    raw = str(relative_path).replace("\\", "/").strip()
    if not raw:
        return None
    path = PurePosixPath(raw)
    if path.is_absolute():
        return None
    if any(part in {"", ".", ".."} for part in path.parts):
        return None
    return path.as_posix()
