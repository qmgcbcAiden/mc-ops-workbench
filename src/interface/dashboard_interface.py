from __future__ import annotations

import logging
from dataclasses import dataclass
from sqlite3 import Connection

from src.ai.assistant_service import AssistantService
from src.ai.context_manager import ContextManager
from src.ai.llm_client import LlmClient
from src.ai.tool_builder import build_config_loop_tool_registry, build_tool_registry
from src.config.settings import Settings
from src.interface.addon_diagnostic_interface import AddonDiagnosticInterface
from src.interface.chat_interface import ChatInterface
from src.interface.command_interface import CommandInterface
from src.interface.config_edit_interface import ConfigEditInterface
from src.interface.config_version_interface import ConfigVersionInterface
from src.interface.file_interface import FileInterface
from src.interface.java_environment_interface import JavaEnvironmentInterface
from src.interface.log_interface import LogInterface
from src.interface.player_interface import PlayerInterface
from src.interface.server_capability_interface import ServerCapabilityInterface
from src.interface.server_interface import ServerInterface
from src.interface.system_interface import SystemInterface
from src.repositories.chat_attachment_repository import ChatAttachmentRepository
from src.repositories.chat_repository import ChatRepository
from src.repositories.chat_summary_repository import ChatSummaryRepository
from src.repositories.command_repository import CommandRepository
from src.repositories.autonomous_task_repository import AutonomousTaskRepository
from src.repositories.app_settings_repository import AppSettingsRepository
from src.repositories.addon_diagnostic_repository import AddonDiagnosticRepository
from src.repositories.config_change_repository import ConfigChangeRepository
from src.repositories.config_version_repository import ConfigVersionRepository
from src.repositories.event_repository import EventRepository
from src.repositories.file_edit_repository import FileEditAuditRepository
from src.repositories.java_environment_repository import JavaEnvironmentRepository
from src.repositories.llm_repository import LlmRepository
from src.repositories.metric_repository import MetricRepository
from src.repositories.player_repository import PlayerRepository
from src.repositories.runtime_repository import ServerRuntimeRepository
from src.service.chat_service import ChatService
from src.service.command_service import CommandService
from src.service.autonomous_config_loop_service import AutonomousConfigLoopService
from src.service.ai_model_service import AiModelService
from src.service.addon_diagnostic_service import AddonDiagnosticService
from src.service.config_edit_service import ConfigEditService
from src.service.config_version_service import ConfigVersionService
from src.service.file_service import FileService
from src.service.java_environment_service import JavaEnvironmentService
from src.service.log_service import LogService
from src.service.player_service import PlayerService
from src.service.server_capability_service import ServerCapabilityService
from src.service.server_service import ServerService
from src.service.system_service import SystemService


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DashboardInterfaces:
    player: PlayerInterface
    log: LogInterface
    system: SystemInterface
    command: CommandInterface
    chat: ChatInterface
    file: FileInterface
    config: ConfigEditInterface
    version: ConfigVersionInterface
    server: ServerInterface
    capability: ServerCapabilityInterface
    java_environment: JavaEnvironmentInterface
    addon: AddonDiagnosticInterface


def create_dashboard_interfaces(
    connection: Connection,
    settings: Settings,
) -> DashboardInterfaces:
    event_repository = EventRepository(connection)
    app_settings_repo = AppSettingsRepository(connection)
    log_service = LogService(event_repository, settings.mc_log_path)
    system_service = SystemService(MetricRepository(connection))
    runtime_repository = ServerRuntimeRepository(connection)
    server_service = ServerService(
        runtime_repository,
        settings,
        event_repository,
        app_settings_repository=app_settings_repo,
    )
    java_environment_service = JavaEnvironmentService(
        settings=settings,
        audit_repository=JavaEnvironmentRepository(connection),
        app_settings_repository=app_settings_repo,
    )
    player_service = PlayerService(
        PlayerRepository(connection),
        settings,
        get_server_state=lambda: server_service.get_status().get("state", "stopped"),
        stdout_source=server_service.get_stdout_lines,
    )
    command_service = CommandService(
        CommandRepository(connection),
        server_service,
        offline_player_command_fallback=player_service.apply_offline_player_command,
    )
    version_repo = ConfigVersionRepository(connection)
    version_service = None
    if settings.config_versioning_enabled:
        version_service = ConfigVersionService(
            repo_dir=settings.config_version_repo_dir,
            version_repo=version_repo,
        )
    file_service = FileService(
        settings,
        edit_repo=FileEditAuditRepository(connection),
        version_service=version_service,
    )
    config_service = ConfigEditService(
        file_service=file_service,
        change_repo=ConfigChangeRepository(connection),
        version_service=version_service,
        source_dir=settings.mc_server_dir,
        auto_approve_max_risk=settings.config_auto_approve_max_risk,
        redaction_version=settings.config_redaction_version,
    )
    addon_service = AddonDiagnosticService(
        settings=settings,
        repository=AddonDiagnosticRepository(connection),
        log_service=log_service,
    )

    chat_repo = ChatRepository(connection)
    summary_repo = ChatSummaryRepository(connection)
    attachment_repo = ChatAttachmentRepository(connection)
    llm_repo = LlmRepository(connection)
    ai_model_service = AiModelService(settings, app_settings_repo)
    llm_client, assistant, context_mgr, autonomous_assistant = _build_ai_components(
        settings=settings,
        ai_model_service=ai_model_service,
        chat_repo=chat_repo,
        summary_repo=summary_repo,
        attachment_repo=attachment_repo,
        llm_repo=llm_repo,
        player_service=player_service,
        log_service=log_service,
        system_service=system_service,
        server_service=server_service,
        command_service=command_service,
        config_service=config_service,
        java_environment_service=java_environment_service,
        addon_service=addon_service,
    )
    autonomous_loop_service = None
    if autonomous_assistant is not None and context_mgr is not None:
        autonomous_loop_service = AutonomousConfigLoopService(
            task_repo=AutonomousTaskRepository(connection),
            assistant_service=autonomous_assistant,
            context_manager=context_mgr,
            config_edit_service=config_service,
            log_service=log_service,
            system_service=system_service,
            server_service=server_service,
            player_service=player_service,
            settings=settings,
        )
    chat_service = ChatService(
        chat_repository=chat_repo,
        player_service=player_service,
        log_service=log_service,
        system_service=system_service,
        llm_client=llm_client,
        assistant_service=assistant,
        context_manager=context_mgr,
        config_edit_service=config_service,
        command_service=command_service,
        server_service=server_service,
        java_environment_service=java_environment_service,
        autonomous_config_loop_service=autonomous_loop_service,
        attachment_repo=attachment_repo,
        summary_repo=summary_repo,
        ai_model_service=ai_model_service,
        addon_service=addon_service,
    )

    return DashboardInterfaces(
        player=PlayerInterface(player_service),
        log=LogInterface(log_service),
        system=SystemInterface(system_service),
        command=CommandInterface(command_service),
        chat=ChatInterface(chat_service),
        file=FileInterface(file_service),
        config=ConfigEditInterface(config_service),
        version=ConfigVersionInterface(version_service),
        server=ServerInterface(server_service),
        capability=ServerCapabilityInterface(ServerCapabilityService(settings)),
        java_environment=JavaEnvironmentInterface(java_environment_service),
        addon=AddonDiagnosticInterface(addon_service),
    )


def _build_ai_components(
    settings: Settings,
    ai_model_service: AiModelService,
    chat_repo: ChatRepository,
    summary_repo: ChatSummaryRepository,
    attachment_repo: ChatAttachmentRepository,
    llm_repo: LlmRepository,
    player_service: PlayerService,
    log_service: LogService,
    system_service: SystemService,
    server_service: ServerService,
    command_service: CommandService,
    config_service: ConfigEditService,
    java_environment_service: JavaEnvironmentService,
    addon_service: AddonDiagnosticService,
) -> tuple[
    LlmClient | None,
    AssistantService | None,
    ContextManager | None,
    AssistantService | None,
]:
    request_config = ai_model_service.get_request_config()
    if request_config.base_url and not request_config.base_url.startswith(("http://", "https://")):
        logger.warning("%s is invalid; AI features are disabled", request_config.base_url_env_name)

    llm_client = LlmClient(
        api_key=request_config.api_key,
        base_url=request_config.base_url,
        model=request_config.model,
        timeout_seconds=request_config.timeout_seconds,
        max_tokens=request_config.max_tokens,
        temperature=request_config.temperature,
        provider=request_config.provider,
        api_key_env_name=request_config.api_key_env_name,
        base_url_env_name=request_config.base_url_env_name,
        model_config_provider=ai_model_service.get_request_config,
    )
    tool_registry = build_tool_registry(
        player_service=player_service,
        log_service=log_service,
        system_service=system_service,
        server_service=server_service,
        command_service=command_service,
        config_service=config_service,
        java_environment_service=java_environment_service,
        addon_service=addon_service,
    )

    assistant = AssistantService(llm_client, tool_registry, llm_repo)
    config_loop_registry = build_config_loop_tool_registry(
        player_service=player_service,
        log_service=log_service,
        system_service=system_service,
        server_service=server_service,
        config_service=config_service,
    )
    autonomous_assistant = AssistantService(llm_client, config_loop_registry, llm_repo)
    context_mgr = ContextManager(
        chat_repo=chat_repo,
        summary_repo=summary_repo,
        attachment_repo=attachment_repo,
        context_max_chars=settings.ai_context_max_chars,
        recent_messages_limit=settings.ai_recent_messages_limit,
        summary_trigger_messages=settings.ai_summary_trigger_messages,
        summary_target_chars=settings.ai_summary_target_chars,
    )
    return llm_client, assistant, context_mgr, autonomous_assistant
