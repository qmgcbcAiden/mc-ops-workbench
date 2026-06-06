from __future__ import annotations

from src.ai.addon_tool_handlers import AddonToolHandlers
from src.ai.config_tool_handlers import ConfigToolHandlers
from src.ai.server_tool_handlers import ServerToolHandlers
from src.ai.tool_registry import ToolRegistry
from src.ai.tool_schemas import (
    CHECK_JAVA_ENVIRONMENT,
    ENSURE_JAVA_ENVIRONMENT,
    EXECUTE_SERVER_COMMAND,
    GET_CONFIG_VALUES,
    GET_ONLINE_PLAYERS,
    GET_SERVER_STATUS,
    GET_ADDON_DIAGNOSTICS,
    LIST_CONFIG_CAPABILITIES,
    PROPOSE_CONFIG_CHANGE,
    PROPOSE_ADDON_REMEDIATION,
    PROPOSE_SERVER_COMMAND,
    QUERY_RECENT_LOGS,
    QUERY_SYSTEM_METRICS,
    READ_CONFIG_FILE,
    RESTART_SERVER,
    SCAN_SERVER_ADDONS,
    START_SERVER,
)
from src.service.addon_diagnostic_service import AddonDiagnosticService
from src.service.command_service import CommandService
from src.service.config_edit_service import ConfigEditService
from src.service.java_environment_service import JavaEnvironmentService
from src.service.log_service import LogService
from src.service.player_service import PlayerService
from src.service.server_service import ServerService
from src.service.system_service import SystemService


def build_tool_registry(
    player_service: PlayerService,
    log_service: LogService,
    system_service: SystemService,
    server_service: ServerService,
    command_service: CommandService,
    config_service: ConfigEditService,
    java_environment_service: JavaEnvironmentService | None = None,
    addon_service: AddonDiagnosticService | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    server_handlers = ServerToolHandlers(
        player_service=player_service,
        log_service=log_service,
        system_service=system_service,
        server_service=server_service,
        command_service=command_service,
        java_environment_service=java_environment_service,
    )
    config_handlers = ConfigToolHandlers(config_service)
    addon_handlers = AddonToolHandlers(addon_service) if addon_service is not None else None

    registry.register(
        "get_online_players",
        server_handlers.get_online_players,
        GET_ONLINE_PLAYERS,
    )
    registry.register(
        "query_system_metrics",
        server_handlers.query_system_metrics,
        QUERY_SYSTEM_METRICS,
    )
    registry.register(
        "query_recent_logs",
        server_handlers.query_recent_logs,
        QUERY_RECENT_LOGS,
    )
    registry.register(
        "get_server_status",
        server_handlers.get_server_status,
        GET_SERVER_STATUS,
    )
    registry.register(
        "check_java_environment",
        server_handlers.check_java_environment,
        CHECK_JAVA_ENVIRONMENT,
    )
    registry.register(
        "ensure_java_environment",
        server_handlers.ensure_java_environment,
        ENSURE_JAVA_ENVIRONMENT,
    )
    registry.register(
        "start_server",
        server_handlers.start_server,
        START_SERVER,
    )
    registry.register(
        "restart_server",
        server_handlers.restart_server,
        RESTART_SERVER,
    )
    registry.register(
        "propose_server_command",
        server_handlers.propose_server_command,
        PROPOSE_SERVER_COMMAND,
    )
    registry.register(
        "execute_server_command",
        server_handlers.execute_server_command,
        EXECUTE_SERVER_COMMAND,
    )
    registry.register(
        "list_config_capabilities",
        config_handlers.list_config_capabilities,
        LIST_CONFIG_CAPABILITIES,
    )
    registry.register(
        "read_config_file",
        config_handlers.read_config_file,
        READ_CONFIG_FILE,
    )
    registry.register(
        "get_config_values",
        config_handlers.get_config_values,
        GET_CONFIG_VALUES,
    )
    registry.register(
        "propose_config_change",
        config_handlers.propose_config_change,
        PROPOSE_CONFIG_CHANGE,
    )
    if addon_handlers is not None:
        registry.register(
            "scan_server_addons",
            addon_handlers.scan_server_addons,
            SCAN_SERVER_ADDONS,
        )
        registry.register(
            "get_addon_diagnostics",
            addon_handlers.get_addon_diagnostics,
            GET_ADDON_DIAGNOSTICS,
        )
        registry.register(
            "propose_addon_remediation",
            addon_handlers.propose_addon_remediation,
            PROPOSE_ADDON_REMEDIATION,
        )
    return registry


def build_config_loop_tool_registry(
    player_service: PlayerService,
    log_service: LogService,
    system_service: SystemService,
    server_service: ServerService,
    config_service: ConfigEditService,
) -> ToolRegistry:
    registry = ToolRegistry()
    server_handlers = ServerToolHandlers(
        player_service=player_service,
        log_service=log_service,
        system_service=system_service,
        server_service=server_service,
        command_service=None,
    )
    config_handlers = ConfigToolHandlers(config_service, allow_auto_apply=False)

    registry.register(
        "get_online_players",
        server_handlers.get_online_players,
        GET_ONLINE_PLAYERS,
    )
    registry.register(
        "query_system_metrics",
        server_handlers.query_system_metrics,
        QUERY_SYSTEM_METRICS,
    )
    registry.register(
        "query_recent_logs",
        server_handlers.query_recent_logs,
        QUERY_RECENT_LOGS,
    )
    registry.register(
        "get_server_status",
        server_handlers.get_server_status,
        GET_SERVER_STATUS,
    )
    registry.register(
        "list_config_capabilities",
        config_handlers.list_config_capabilities,
        LIST_CONFIG_CAPABILITIES,
    )
    registry.register(
        "read_config_file",
        config_handlers.read_config_file,
        READ_CONFIG_FILE,
    )
    registry.register(
        "get_config_values",
        config_handlers.get_config_values,
        GET_CONFIG_VALUES,
    )
    registry.register(
        "propose_config_change",
        config_handlers.propose_config_change,
        PROPOSE_CONFIG_CHANGE,
    )
    return registry
