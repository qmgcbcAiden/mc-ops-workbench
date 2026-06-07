from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Iterable

from src.config.settings import Settings
from src.mc.server_capabilities import detect_server_core
from src.repositories.app_settings_repository import AppSettingsRepository
from src.repositories.java_environment_repository import JavaEnvironmentRepository


MC_JAVA_PATH_OVERRIDE_KEY = "mc_java_path_override"
MC_JAVA_HOME_OVERRIDE_KEY = "mc_java_home_override"
MC_SERVER_VERSION_OVERRIDE_KEY = "mc_server_version_override"
ADOPTIUM_API_BASE_URL = "https://api.adoptium.net/v3"
_ADOPTIUM_USER_AGENT = "MinecraftServerDashboard/1.0"
_JAVA_VERSION_RE = re.compile(r'version\s+"([^"]+)"|openjdk\s+([0-9][^\s"]*)', re.IGNORECASE)
_MC_VERSION_RE = re.compile(r"(?<!\d)(1\.\d+(?:\.\d+)?)(?!\d)")
_CHECK_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class JavaCandidate:
    java_path: Path
    java_home: Path | None
    major: int
    version_text: str
    source: str
    portable: bool = False

    def to_dict(self) -> dict:
        return {
            "java_path": str(self.java_path),
            "java_home": str(self.java_home) if self.java_home else None,
            "major": self.major,
            "version_text": self.version_text,
            "source": self.source,
            "portable": self.portable,
        }


@dataclass(frozen=True)
class JavaRequirement:
    minecraft_version: str | None
    required_java_major: int | None
    source: str
    needs_input: bool
    metadata: dict[str, Any]


class JavaEnvironmentService:
    def __init__(
        self,
        settings: Settings,
        audit_repository: JavaEnvironmentRepository,
        app_settings_repository: AppSettingsRepository,
        *,
        installer: "PortableJavaInstaller | None" = None,
        version_runner: Callable[[Path], str | None] | None = None,
        environ: dict[str, str] | None = None,
    ) -> None:
        self._settings = settings
        self._audit_repo = audit_repository
        self._app_settings = app_settings_repository
        self._version_runner = version_runner or _run_java_version
        self._environ = environ if environ is not None else os.environ
        self._installer = installer or PortableJavaInstaller(
            settings=settings,
            version_runner=self._version_runner,
        )

    def check_environment(self, server_version: str | None = None) -> dict:
        requirement = self._resolve_requirement(server_version)
        result = self._result_for_requirement(requirement, action="check")
        self._record_audit("check", result)
        return result

    def ensure_environment(
        self,
        server_version: str | None = None,
        *,
        start_after_ready: bool = False,
    ) -> dict:
        requirement = self._resolve_requirement(server_version)
        if requirement.needs_input or requirement.required_java_major is None:
            result = self._base_result(
                status="needs_input",
                requirement=requirement,
                message="无法识别 Minecraft 版本，请告诉我服务端版本，例如 1.20.1。",
                metadata={"start_after_ready": start_after_ready},
            )
            self._record_audit("ensure", result)
            return result

        candidates = self._discover_candidates()
        selected = _select_exact_candidate(candidates, requirement.required_java_major)
        if selected is not None:
            self._persist_selected_java(selected, requirement.minecraft_version)
            result = self._base_result(
                status="selected",
                requirement=requirement,
                selected=selected,
                candidates=candidates,
                message=(
                    f"已选择 Java {selected.major}：{selected.java_path}。"
                ),
                metadata={
                    "start_after_ready": start_after_ready,
                    "script_warning": self._custom_start_script_warning(),
                },
            )
            self._record_audit("select", result)
            return result

        if not self._settings.java_auto_install_enabled:
            result = self._base_result(
                status="failed",
                requirement=requirement,
                candidates=candidates,
                message="未找到匹配 Java，且自动安装已关闭。",
                error_message="JAVA_AUTO_INSTALL_ENABLED=false",
                metadata={"start_after_ready": start_after_ready},
            )
            self._record_audit("ensure", result)
            return result

        try:
            installed = self._installer.install(
                required_major=requirement.required_java_major,
                package_preference=self._settings.java_package_type,
            )
        except JavaInstallError as exc:
            result = self._base_result(
                status="failed",
                requirement=requirement,
                candidates=candidates,
                message=f"Java 自动安装失败：{exc}",
                error_message=str(exc),
                metadata={"start_after_ready": start_after_ready},
            )
            self._record_audit("install", result)
            return result

        self._persist_selected_java(installed, requirement.minecraft_version)
        result = self._base_result(
            status="installed",
            requirement=requirement,
            selected=installed,
            candidates=candidates + [installed],
            message=f"已安装并选择 Java {installed.major}：{installed.java_path}。",
            metadata={
                "start_after_ready": start_after_ready,
                "script_warning": self._custom_start_script_warning(),
            },
        )
        self._record_audit("install", result)
        return result

    def effective_settings(self) -> Settings:
        return settings_with_java_overrides(self._settings, self._app_settings)

    def _result_for_requirement(self, requirement: JavaRequirement, action: str) -> dict:
        if requirement.needs_input or requirement.required_java_major is None:
            return self._base_result(
                status="needs_input",
                requirement=requirement,
                message="无法识别 Minecraft 版本，请告诉我服务端版本，例如 1.20.1。",
                metadata={"action": action},
            )
        candidates = self._discover_candidates()
        current = _current_effective_candidate(
            candidates,
            self.effective_settings().mc_java_path,
        )
        selected = (
            current
            if current is not None and current.major == requirement.required_java_major
            else _select_exact_candidate(candidates, requirement.required_java_major)
        )
        status = "ok" if current is selected and selected is not None else (
            "selected" if selected is not None else "failed"
        )
        message = (
            f"当前 Java 已匹配 Minecraft {requirement.minecraft_version or '?'}：Java {selected.major}。"
            if status == "ok" and selected is not None
            else f"发现可用 Java {selected.major}，可通过修复操作切换到：{selected.java_path}。"
            if status == "selected" and selected is not None
            else f"未找到 Java {requirement.required_java_major}，可通过修复操作自动安装。"
        )
        return self._base_result(
            status=status,
            requirement=requirement,
            selected=selected,
            candidates=candidates,
            message=message,
            metadata={"action": action},
        )

    def _resolve_requirement(self, server_version: str | None) -> JavaRequirement:
        explicit_version = _normalize_mc_version(server_version)
        if explicit_version is not None:
            self._app_settings.set(MC_SERVER_VERSION_OVERRIDE_KEY, explicit_version)
            major = java_major_for_minecraft_version(explicit_version)
            return JavaRequirement(
                minecraft_version=explicit_version,
                required_java_major=major,
                source="user_input",
                needs_input=major is None,
                metadata={"server_version": explicit_version},
            )

        stored_version = _normalize_mc_version(
            self._app_settings.get(MC_SERVER_VERSION_OVERRIDE_KEY)
        )
        if stored_version is not None:
            major = java_major_for_minecraft_version(stored_version)
            return JavaRequirement(
                minecraft_version=stored_version,
                required_java_major=major,
                source="sqlite_override",
                needs_input=major is None,
                metadata={"server_version": stored_version},
            )

        core = detect_server_core(self._settings.mc_server_dir, self._settings.mc_server_jar)
        core_version = _normalize_mc_version(core.version)
        if core_version is not None:
            major = java_major_for_minecraft_version(core_version)
            return JavaRequirement(
                minecraft_version=core_version,
                required_java_major=major,
                source=f"server_core:{core.source}",
                needs_input=major is None,
                metadata={"server_core": core.to_dict()},
            )

        metadata_requirement = self._requirement_from_jar_metadata()
        if metadata_requirement is not None:
            return metadata_requirement

        log_version = self._version_from_latest_log()
        if log_version is not None:
            major = java_major_for_minecraft_version(log_version)
            return JavaRequirement(
                minecraft_version=log_version,
                required_java_major=major,
                source="latest_log",
                needs_input=major is None,
                metadata={"server_version": log_version},
            )

        return JavaRequirement(
            minecraft_version=None,
            required_java_major=None,
            source="unknown",
            needs_input=True,
            metadata={},
        )

    def _requirement_from_jar_metadata(self) -> JavaRequirement | None:
        jar = self._settings.mc_server_jar
        if not jar.is_file():
            return None
        try:
            with zipfile.ZipFile(jar) as archive:
                metadata = _read_json_member(archive, "version.json")
                if metadata:
                    version = _normalize_mc_version(str(metadata.get("id") or ""))
                    java_version = metadata.get("javaVersion")
                    java_major = None
                    if isinstance(java_version, dict):
                        java_major = _as_int(java_version.get("majorVersion"))
                    if java_major is None and version is not None:
                        java_major = java_major_for_minecraft_version(version)
                    if version is not None or java_major is not None:
                        return JavaRequirement(
                            minecraft_version=version,
                            required_java_major=java_major,
                            source="jar:version.json",
                            needs_input=java_major is None,
                            metadata={"version_json": metadata},
                        )

                manifest = _read_text_member(archive, "META-INF/MANIFEST.MF")
                version = _normalize_mc_version(manifest or "")
                if version is not None:
                    major = java_major_for_minecraft_version(version)
                    return JavaRequirement(
                        minecraft_version=version,
                        required_java_major=major,
                        source="jar:manifest",
                        needs_input=major is None,
                        metadata={"manifest_detected_version": version},
                    )
        except (OSError, zipfile.BadZipFile):
            return None
        return None

    def _version_from_latest_log(self) -> str | None:
        path = self._settings.mc_log_path
        try:
            content = path.read_text(encoding="utf-8", errors="replace")[-12000:]
        except (OSError, UnicodeError):
            return None
        patterns = (
            r"Starting minecraft server version\s+(1\.\d+(?:\.\d+)?)",
            r"Minecraft version[:\s]+(1\.\d+(?:\.\d+)?)",
            r"server version\s+(?:[^0-9]+)?(1\.\d+(?:\.\d+)?)",
        )
        for pattern in patterns:
            match = re.search(pattern, content, flags=re.IGNORECASE)
            if match:
                return _normalize_mc_version(match.group(1))
        return _normalize_mc_version(content)

    def _discover_candidates(self) -> list[JavaCandidate]:
        paths: list[tuple[Path, str, bool]] = []
        effective = self.effective_settings()
        paths.extend(_paths_from_java_setting(effective.mc_java_path, "configured_java"))
        paths.extend(_paths_from_java_home(self._environ.get("JAVA_HOME"), "java_home"))
        paths.extend(_paths_from_path_env(self._environ.get("PATH", "")))
        paths.extend(_portable_java_paths(self._settings.java_auto_install_dir))
        if os.name == "nt":
            paths.extend(_windows_common_java_paths())
        elif sys_platform() == "darwin":
            paths.extend(_macos_java_home_paths())
        else:
            paths.extend(_linux_jvm_paths())

        candidates: list[JavaCandidate] = []
        seen: set[str] = set()
        for java_path, source, portable in paths:
            resolved = _resolve_path_key(java_path)
            if resolved in seen:
                continue
            seen.add(resolved)
            candidate = self._candidate_from_path(java_path, source, portable)
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    def _candidate_from_path(
        self,
        java_path: Path,
        source: str,
        portable: bool,
    ) -> JavaCandidate | None:
        output = self._version_runner(java_path)
        if not output:
            return None
        major = parse_java_major(output)
        if major is None:
            return None
        return JavaCandidate(
            java_path=java_path,
            java_home=_java_home_for_path(java_path),
            major=major,
            version_text=_compact_java_version(output),
            source=source,
            portable=portable,
        )

    def _persist_selected_java(
        self,
        candidate: JavaCandidate,
        minecraft_version: str | None,
    ) -> None:
        self._app_settings.set(MC_JAVA_PATH_OVERRIDE_KEY, str(candidate.java_path))
        if candidate.java_home is not None:
            self._app_settings.set(MC_JAVA_HOME_OVERRIDE_KEY, str(candidate.java_home))
        if minecraft_version:
            self._app_settings.set(MC_SERVER_VERSION_OVERRIDE_KEY, minecraft_version)

    def _base_result(
        self,
        *,
        status: str,
        requirement: JavaRequirement,
        message: str,
        selected: JavaCandidate | None = None,
        candidates: list[JavaCandidate] | None = None,
        error_message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict:
        return {
            "status": status,
            "minecraft_version": requirement.minecraft_version,
            "required_java_major": requirement.required_java_major,
            "requirement_source": requirement.source,
            "selected_java": selected.to_dict() if selected else None,
            "candidates": [candidate.to_dict() for candidate in candidates or []],
            "distribution": self._settings.java_distribution,
            "package_type": self._settings.java_package_type,
            "message": message,
            "error_message": error_message,
            "metadata": {
                **requirement.metadata,
                **(metadata or {}),
            },
        }

    def _record_audit(self, action: str, result: dict) -> None:
        selected = result.get("selected_java") or {}
        self._audit_repo.create_audit(
            action=action,
            status=str(result.get("status") or "unknown"),
            minecraft_version=result.get("minecraft_version"),
            required_java_major=result.get("required_java_major"),
            selected_java_path=selected.get("java_path"),
            selected_java_home=selected.get("java_home"),
            candidate_source=selected.get("source"),
            distribution=result.get("distribution"),
            package_type=result.get("package_type"),
            error_message=result.get("error_message"),
            metadata={
                "requirement_source": result.get("requirement_source"),
                "candidate_count": len(result.get("candidates") or []),
                **(result.get("metadata") or {}),
            },
        )

    def _custom_start_script_warning(self) -> str | None:
        from src.mc.server_process import discover_start_scripts, is_managed_start_script

        scripts = discover_start_scripts(self._settings.mc_server_dir)
        if not scripts:
            return None
        script_path = scripts[0]
        if is_managed_start_script(script_path):
            return None
        return (
            f"{script_path.name} 看起来是自定义启动脚本。应用会设置 PATH/JAVA_HOME，"
            "但脚本里的绝对 Java 路径可能绕过本次选择。"
        )


class JavaInstallError(RuntimeError):
    pass


class PortableJavaInstaller:
    def __init__(
        self,
        settings: Settings,
        *,
        version_runner: Callable[[Path], str | None] | None = None,
        downloader: "AdoptiumDownloader | None" = None,
    ) -> None:
        self._settings = settings
        self._version_runner = version_runner or _run_java_version
        self._downloader = downloader or AdoptiumDownloader()

    def install(self, required_major: int, package_preference: str = "jre") -> JavaCandidate:
        package_order = _package_order(package_preference)
        last_error: Exception | None = None
        for package_type in package_order:
            try:
                return self._install_package(required_major, package_type)
            except JavaInstallError as exc:
                last_error = exc
        raise JavaInstallError(str(last_error) if last_error else "未找到可用 Java 下载资产。")

    def _install_package(self, required_major: int, package_type: str) -> JavaCandidate:
        os_name = adoptium_os()
        arch = adoptium_arch()
        asset = self._downloader.resolve_asset(required_major, os_name, arch, package_type)
        if asset is None:
            raise JavaInstallError(f"Temurin 没有可用的 {package_type} 资产。")

        install_root = self._settings.java_auto_install_dir
        install_root.mkdir(parents=True, exist_ok=True)
        final_home = install_root / f"temurin-{required_major}-{os_name}-{arch}-{package_type}"

        with tempfile.TemporaryDirectory(prefix="java-download-", dir=str(install_root)) as tmp:
            tmp_dir = Path(tmp)
            archive_path = tmp_dir / asset.filename
            self._downloader.download(asset.link, archive_path)
            _verify_checksum(archive_path, asset.checksum)

            extract_dir = tmp_dir / "extract"
            extract_dir.mkdir()
            _extract_archive(archive_path, extract_dir)
            java_path = _find_java_executable(extract_dir)
            if java_path is None:
                raise JavaInstallError("下载包中没有找到 Java 可执行文件。")
            java_home = _java_home_for_path(java_path)
            if java_home is None:
                raise JavaInstallError("无法识别下载包中的 JAVA_HOME。")

            output = self._version_runner(java_path)
            major = parse_java_major(output or "")
            if major != required_major:
                raise JavaInstallError(
                    f"下载的 Java 版本不匹配：需要 {required_major}，实际 {major or 'unknown'}。"
                )

            if final_home.exists():
                shutil.rmtree(final_home)
            shutil.move(str(java_home), str(final_home))

        final_java = _java_executable_for_home(final_home)
        output = self._version_runner(final_java)
        major = parse_java_major(output or "")
        if major != required_major:
            raise JavaInstallError("安装后的 Java 校验失败。")
        return JavaCandidate(
            java_path=final_java,
            java_home=final_home,
            major=major,
            version_text=_compact_java_version(output or ""),
            source="portable_install",
            portable=True,
        )


@dataclass(frozen=True)
class AdoptiumAsset:
    link: str
    filename: str
    checksum: str
    package_type: str


class AdoptiumDownloader:
    def __init__(self, base_url: str = ADOPTIUM_API_BASE_URL) -> None:
        self._base_url = base_url.rstrip("/")

    def resolve_asset(
        self,
        feature_version: int,
        os_name: str,
        arch: str,
        package_type: str,
    ) -> AdoptiumAsset | None:
        url = (
            f"{self._base_url}/assets/latest/{feature_version}/hotspot"
            f"?architecture={arch}&image_type={package_type}&os={os_name}&vendor=eclipse"
        )
        try:
            request = self._request(url, accept="application/json")
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise JavaInstallError(
                f"无法查询 Temurin 下载资产：{_format_http_error(exc)}"
            ) from exc
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise JavaInstallError(f"无法查询 Temurin 下载资产：{exc}") from exc

        for item in payload:
            package = (item.get("binary") or {}).get("package") or {}
            link = package.get("link")
            checksum = package.get("checksum")
            checksum_link = package.get("checksum_link")
            filename = package.get("name") or (Path(str(link)).name if link else "")
            if not link or not filename:
                continue
            if not checksum and checksum_link:
                checksum = self._read_text(checksum_link).split()[0]
            if not checksum:
                continue
            return AdoptiumAsset(
                link=self._binary_url(feature_version, os_name, arch, package_type),
                filename=str(filename),
                checksum=str(checksum).strip(),
                package_type=package_type,
            )
        return None

    def download(self, url: str, target: Path) -> None:
        for attempt in range(2):
            try:
                request = self._request(url, accept="application/octet-stream")
                with urllib.request.urlopen(request, timeout=60) as response:
                    with target.open("wb") as handle:
                        shutil.copyfileobj(response, handle)
                return
            except urllib.error.HTTPError as exc:
                if exc.code == 403 and attempt == 0:
                    continue
                raise JavaInstallError(f"Java 下载失败：{_format_http_error(exc)}") from exc
            except (OSError, urllib.error.URLError) as exc:
                raise JavaInstallError(f"Java 下载失败：{exc}") from exc

    def _read_text(self, url: str) -> str:
        try:
            request = self._request(url, accept="text/plain")
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raise JavaInstallError(f"无法读取 checksum：{_format_http_error(exc)}") from exc
        except (OSError, urllib.error.URLError) as exc:
            raise JavaInstallError(f"无法读取 checksum：{exc}") from exc

    def _binary_url(
        self,
        feature_version: int,
        os_name: str,
        arch: str,
        package_type: str,
    ) -> str:
        return (
            f"{self._base_url}/binary/latest/{feature_version}/ga/{os_name}/{arch}/"
            f"{package_type}/hotspot/normal/eclipse"
        )

    @staticmethod
    def _request(url: str, *, accept: str) -> urllib.request.Request:
        return urllib.request.Request(
            url,
            headers={
                "Accept": accept,
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "User-Agent": _ADOPTIUM_USER_AGENT,
            },
        )


def _format_http_error(exc: urllib.error.HTTPError) -> str:
    host = urllib.parse.urlsplit(exc.url).hostname or "unknown host"
    reason = str(exc.reason or "").strip()
    retry_after = exc.headers.get("Retry-After") if exc.headers else None
    detail = f"HTTP {exc.code}"
    if reason:
        detail += f" {reason}"
    detail += f"（来源：{host}）"
    if retry_after:
        detail += f"，请在 {retry_after} 秒后重试"
    return detail


def settings_with_java_overrides(
    settings: Settings,
    app_settings: AppSettingsRepository | None,
) -> Settings:
    if app_settings is None:
        return settings
    java_path = app_settings.get(MC_JAVA_PATH_OVERRIDE_KEY)
    if not java_path:
        return settings
    return replace(settings, mc_java_path=java_path)


def java_major_for_minecraft_version(version: str | None) -> int | None:
    parsed = _parse_mc_version(version)
    if parsed is None:
        return None
    major, minor, patch = parsed
    if major != 1:
        return None
    if minor > 20 or (minor == 20 and patch >= 5):
        return 21
    if minor >= 18:
        return 17
    if minor == 17:
        return 16
    if 12 <= minor <= 16:
        return 8
    return None


def parse_java_major(output: str) -> int | None:
    match = _JAVA_VERSION_RE.search(output or "")
    if not match:
        return None
    version = match.group(1) or match.group(2) or ""
    first = version.split(".", 2)[0]
    second = version.split(".", 2)[1] if "." in version else ""
    if first == "1" and second.isdigit():
        return int(second)
    return int(first) if first.isdigit() else None


def adoptium_os() -> str:
    system = platform.system().lower()
    if system.startswith("darwin"):
        return "mac"
    if system.startswith("windows"):
        return "windows"
    return "linux"


def adoptium_arch() -> str:
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        return "x64"
    if machine in {"aarch64", "arm64"}:
        return "aarch64"
    if machine.startswith("arm"):
        return "arm"
    return machine or "x64"


def sys_platform() -> str:
    return platform.system().lower()


def _run_java_version(java_path: Path) -> str | None:
    try:
        completed = subprocess.run(
            [str(java_path), "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=_CHECK_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout.decode("utf-8", errors="replace")


def _normalize_mc_version(value: str | None) -> str | None:
    if not value:
        return None
    match = _MC_VERSION_RE.search(value)
    return match.group(1) if match else None


def _parse_mc_version(version: str | None) -> tuple[int, int, int] | None:
    normalized = _normalize_mc_version(version)
    if normalized is None:
        return None
    parts = [int(part) for part in normalized.split(".")]
    while len(parts) < 3:
        parts.append(0)
    return parts[0], parts[1], parts[2]


def _as_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _read_json_member(archive: zipfile.ZipFile, name: str) -> dict | None:
    try:
        raw = archive.read(name)
    except KeyError:
        return None
    try:
        value = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _read_text_member(archive: zipfile.ZipFile, name: str) -> str | None:
    try:
        return archive.read(name).decode("utf-8", errors="replace")
    except KeyError:
        return None


def _paths_from_java_setting(value: str, source: str) -> list[tuple[Path, str, bool]]:
    if not value:
        return []
    path = Path(value)
    if path.is_absolute() or path.parent != Path("."):
        return [(path, source, False)]
    resolved = shutil.which(value)
    return [(Path(resolved), source, False)] if resolved else []


def _paths_from_java_home(value: str | None, source: str) -> list[tuple[Path, str, bool]]:
    if not value:
        return []
    java_path = _java_executable_for_home(Path(value))
    return [(java_path, source, False)]


def _paths_from_path_env(path_env: str) -> list[tuple[Path, str, bool]]:
    results: list[tuple[Path, str, bool]] = []
    executable_name = "java.exe" if os.name == "nt" else "java"
    for item in path_env.split(os.pathsep):
        if not item:
            continue
        path = Path(item) / executable_name
        if path.exists():
            results.append((path, "path", False))
    return results


def _portable_java_paths(root: Path) -> list[tuple[Path, str, bool]]:
    if not root.is_dir():
        return []
    results: list[tuple[Path, str, bool]] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        java_path = _java_executable_for_home(child)
        if java_path.exists():
            results.append((java_path, "portable_cache", True))
    return results


def _macos_java_home_paths() -> list[tuple[Path, str, bool]]:
    try:
        completed = subprocess.run(
            ["/usr/libexec/java_home", "-V"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=_CHECK_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    output = completed.stdout.decode("utf-8", errors="replace")
    homes: list[tuple[Path, str, bool]] = []
    for line in output.splitlines():
        match = re.search(r"(/\S.*?/Contents/Home)", line)
        if match:
            homes.append((_java_executable_for_home(Path(match.group(1))), "macos_java_home", False))
    return homes


def _windows_common_java_paths() -> list[tuple[Path, str, bool]]:
    roots = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
    ]
    results: list[tuple[Path, str, bool]] = []
    for root in roots:
        if not root:
            continue
        for base in (Path(root) / "Eclipse Adoptium", Path(root) / "Java"):
            if not base.is_dir():
                continue
            results.extend(
                (path, "windows_common", False)
                for path in _iter_java_executables(base, max_depth=4)
            )
    return results


def _linux_jvm_paths() -> list[tuple[Path, str, bool]]:
    base = Path("/usr/lib/jvm")
    if not base.is_dir():
        return []
    return [
        (path, "linux_jvm", False)
        for path in _iter_java_executables(base, max_depth=3)
    ]


def _iter_java_executables(root: Path, max_depth: int) -> Iterable[Path]:
    executable_name = "java.exe" if os.name == "nt" else "java"
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        candidate = current / "bin" / executable_name
        if candidate.exists():
            yield candidate
        if depth >= max_depth:
            continue
        try:
            children = list(current.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_dir():
                stack.append((child, depth + 1))


def _java_executable_for_home(home: Path) -> Path:
    executable_name = "java.exe" if os.name == "nt" else "java"
    return home / "bin" / executable_name


def _java_home_for_path(java_path: Path) -> Path | None:
    if java_path.name.lower() not in {"java", "java.exe"}:
        return None
    if java_path.parent.name != "bin":
        return None
    return java_path.parent.parent


def _resolve_path_key(path: Path) -> str:
    try:
        return str(path.resolve(strict=False))
    except OSError:
        return str(path)


def _current_effective_candidate(
    candidates: list[JavaCandidate],
    java_setting: str,
) -> JavaCandidate | None:
    paths = _paths_from_java_setting(java_setting, "configured_java")
    if not paths:
        return None
    key = _resolve_path_key(paths[0][0])
    return next(
        (candidate for candidate in candidates if _resolve_path_key(candidate.java_path) == key),
        None,
    )


def _select_exact_candidate(
    candidates: list[JavaCandidate],
    required_major: int,
) -> JavaCandidate | None:
    system = [
        candidate
        for candidate in candidates
        if candidate.major == required_major and not candidate.portable
    ]
    if system:
        return system[0]
    portable = [
        candidate
        for candidate in candidates
        if candidate.major == required_major and candidate.portable
    ]
    return portable[0] if portable else None


def _package_order(preference: str) -> list[str]:
    normalized = (preference or "jre").strip().lower()
    if normalized == "jdk":
        return ["jdk"]
    return ["jre", "jdk"]


def _verify_checksum(path: Path, expected_sha256: str) -> None:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest().lower()
    expected = expected_sha256.strip().lower()
    if actual != expected:
        raise JavaInstallError("Java 下载包 checksum 校验失败。")


def _extract_archive(archive_path: Path, target_dir: Path) -> None:
    suffixes = "".join(archive_path.suffixes).lower()
    try:
        if suffixes.endswith(".zip"):
            with zipfile.ZipFile(archive_path) as archive:
                _safe_extract_zip(archive, target_dir)
            return
        if suffixes.endswith((".tar.gz", ".tgz")):
            with tarfile.open(archive_path) as archive:
                _safe_extract_tar(archive, target_dir)
            return
    except (OSError, zipfile.BadZipFile, tarfile.TarError) as exc:
        raise JavaInstallError(f"Java 下载包解压失败：{exc}") from exc
    raise JavaInstallError(f"不支持的 Java 下载包格式：{archive_path.name}")


def _find_java_executable(root: Path) -> Path | None:
    executable_name = "java.exe" if os.name == "nt" else "java"
    for path in root.rglob(executable_name):
        if path.parent.name == "bin":
            return path
    return None


def _safe_extract_zip(archive: zipfile.ZipFile, target_dir: Path) -> None:
    target_root = target_dir.resolve(strict=False)
    for member in archive.infolist():
        target = (target_dir / member.filename).resolve(strict=False)
        if not _is_within(target, target_root):
            raise JavaInstallError("Java 下载包包含不安全的路径。")
    archive.extractall(target_dir)


def _safe_extract_tar(archive: tarfile.TarFile, target_dir: Path) -> None:
    target_root = target_dir.resolve(strict=False)
    for member in archive.getmembers():
        target = (target_dir / member.name).resolve(strict=False)
        if not _is_within(target, target_root):
            raise JavaInstallError("Java 下载包包含不安全的路径。")
    archive.extractall(target_dir)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _compact_java_version(output: str) -> str:
    lines = [line.strip() for line in (output or "").splitlines() if line.strip()]
    return lines[0] if lines else ""
