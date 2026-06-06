"""OpenAI Function Calling tool schemas."""

GET_SERVER_STATUS = {
    "type": "function",
    "function": {
        "name": "get_server_status",
        "description": "查询 Minecraft 服务器当前运行状态，包括是否在线、PID、运行时长、玩家数等。",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
}

CHECK_JAVA_ENVIRONMENT = {
    "type": "function",
    "function": {
        "name": "check_java_environment",
        "description": (
            "检查本地 Java 环境是否适合当前 Minecraft 服务端。"
            "可选传入用户提供的 Minecraft 版本；该工具只诊断和记录审计，不下载安装。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "server_version": {
                    "type": "string",
                    "description": "可选的 Minecraft 版本，例如 1.20.1。",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
    },
}

ENSURE_JAVA_ENVIRONMENT = {
    "type": "function",
    "function": {
        "name": "ensure_java_environment",
        "description": (
            "确保本地 Java 环境适合当前 Minecraft 服务端。"
            "优先选择系统中匹配的 Java；找不到时通过受控服务安装项目内便携 Temurin。"
            "如果 start_after_ready 为 true，环境就绪后会继续调用受控 start_server。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "server_version": {
                    "type": "string",
                    "description": "可选的 Minecraft 版本，例如 1.20.1。",
                },
                "start_after_ready": {
                    "type": "boolean",
                    "description": "环境就绪后是否继续启动服务器。",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
    },
}

START_SERVER = {
    "type": "function",
    "function": {
        "name": "start_server",
        "description": (
            "通过受控 ServerService 启动本地 Minecraft 服务器进程。"
            "该工具只用于用户明确要求启动服务器时；不要用 shell 命令启动。"
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
}

RESTART_SERVER = {
    "type": "function",
    "function": {
        "name": "restart_server",
        "description": (
            "创建一个需要用户确认的 Minecraft 服务器重启操作。"
            "该工具不会立即停止或启动服务器；确认后本地服务会先停止服务器，"
            "待停止完成后再通过 start_server 启动。"
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
}

QUERY_RECENT_LOGS = {
    "type": "function",
    "function": {
        "name": "query_recent_logs",
        "description": "查询最近的 Minecraft 服务器结构化日志，可按日志级别过滤。",
        "parameters": {
            "type": "object",
            "properties": {
                "level": {
                    "type": "string",
                    "enum": ["INFO", "WARN", "ERROR"],
                    "description": "日志级别过滤，不传则返回所有级别。",
                },
                "limit": {
                    "type": "integer",
                    "description": "返回条数上限，默认 20。",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
    },
}

QUERY_SYSTEM_METRICS = {
    "type": "function",
    "function": {
        "name": "query_system_metrics",
        "description": "查询系统 CPU 和内存使用情况，包括 Java 进程占比。",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
}

GET_ONLINE_PLAYERS = {
    "type": "function",
    "function": {
        "name": "get_online_players",
        "description": "查询当前 Minecraft 服务器在线的玩家列表。",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
}

PROPOSE_SERVER_COMMAND = {
    "type": "function",
    "function": {
        "name": "propose_server_command",
        "description": (
            "评估一条 Minecraft 服务器命令的风险等级，但不执行。"
            "返回风险等级（LOW/MEDIUM/HIGH/BLOCKED）和说明。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "待评估的 Minecraft 服务器命令。",
                },
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    },
}

EXECUTE_SERVER_COMMAND = {
    "type": "function",
    "function": {
        "name": "execute_server_command",
        "description": (
            "通过受控 CommandService 向 Minecraft 服务器发送一条控制台命令。"
            "LOW/MEDIUM 风险命令可执行；HIGH 风险命令只会返回需要用户确认；"
            "BLOCKED 命令永远不会执行。所有请求都会写入 command_audits。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "待发送到 Minecraft 控制台的命令，不要包含前导斜杠。",
                },
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    },
}

LIST_CONFIG_CAPABILITIES = {
    "type": "function",
    "function": {
        "name": "list_config_capabilities",
        "description": "查询当前允许 AI 辅助修改的 Minecraft 配置文件和配置项。",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
}

READ_CONFIG_FILE = {
    "type": "function",
    "function": {
        "name": "read_config_file",
        "description": (
            "读取白名单内 Minecraft 配置文件的安全快照。"
            "只返回允许暴露的配置项和值，不返回非白名单或敏感项全文。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "配置文件相对路径，例如 server.properties。",
                },
            },
            "required": ["file"],
            "additionalProperties": False,
        },
    },
}

GET_CONFIG_VALUES = {
    "type": "function",
    "function": {
        "name": "get_config_values",
        "description": "读取白名单内 Minecraft 配置项的当前值。",
        "parameters": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "配置文件相对路径，例如 server.properties。",
                },
                "keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要查询的配置项 key。",
                },
            },
            "required": ["file", "keys"],
            "additionalProperties": False,
        },
    },
}

PROPOSE_CONFIG_CHANGE = {
    "type": "function",
    "function": {
        "name": "propose_config_change",
        "description": (
            "根据明确的目标配置项和值生成 Minecraft 配置修改草案。"
            "该工具只生成草案和 diff，不保存文件。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "配置文件相对路径，例如 server.properties。",
                },
                "changes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "key": {"type": "string"},
                            "value": {
                                "type": "string",
                                "description": "目标值，统一先以字符串传入，由本地服务做类型校验。",
                            },
                            "reason": {
                                "type": "string",
                                "description": "用户自然语言目标对应到该配置项的原因。",
                            },
                        },
                        "required": ["key", "value"],
                        "additionalProperties": False,
                    },
                },
                "user_request": {
                    "type": "string",
                    "description": "用户原始自然语言请求，用于审计和解释。",
                },
                "session_id": {
                    "type": "string",
                    "description": "可选，会话 ID，用于把配置草案关联到当前聊天审计记录。",
                },
            },
            "required": ["file", "changes", "user_request"],
            "additionalProperties": False,
        },
    },
}

SCAN_SERVER_ADDONS = {
    "type": "function",
    "function": {
        "name": "scan_server_addons",
        "description": (
            "扫描 Minecraft 服务端 mods/ 和 plugins/ 中的 jar，"
            "基于本地元数据、依赖图和运行时日志生成冲突诊断。"
            "AI 调用该工具时始终使用本地扫描；需要联网刷新元数据时，"
            "必须由用户在 UI 或本地意图路由中明确请求。"
            "该工具只读文件并写审计，不移动、不删除、不修改组件。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "refresh_online": {
                    "type": "boolean",
                    "description": (
                        "保留字段。AI 调用会被服务端强制视为 false；"
                        "联网刷新只能由明确用户请求触发。"
                    ),
                },
            },
            "required": [],
            "additionalProperties": False,
        },
    },
}

GET_ADDON_DIAGNOSTICS = {
    "type": "function",
    "function": {
        "name": "get_addon_diagnostics",
        "description": "读取最近一次 Mod/插件诊断报告和诊断项，可按严重级别过滤。",
        "parameters": {
            "type": "object",
            "properties": {
                "severity": {
                    "type": "string",
                    "enum": ["BLOCKER", "HIGH", "MEDIUM", "LOW", "INFO"],
                    "description": "可选严重级别过滤。",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
    },
}

PROPOSE_ADDON_REMEDIATION = {
    "type": "function",
    "function": {
        "name": "propose_addon_remediation",
        "description": (
            "根据诊断项生成受控修复草案。"
            "首版只生成草案；不会自动移动、删除、禁用或编辑任何文件。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "diagnostic_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要生成修复草案的诊断项 ID 列表。",
                },
            },
            "required": ["diagnostic_ids"],
            "additionalProperties": False,
        },
    },
}

ALL_TOOLS = [
    GET_SERVER_STATUS,
    CHECK_JAVA_ENVIRONMENT,
    ENSURE_JAVA_ENVIRONMENT,
    START_SERVER,
    RESTART_SERVER,
    QUERY_RECENT_LOGS,
    QUERY_SYSTEM_METRICS,
    GET_ONLINE_PLAYERS,
    PROPOSE_SERVER_COMMAND,
    EXECUTE_SERVER_COMMAND,
    LIST_CONFIG_CAPABILITIES,
    READ_CONFIG_FILE,
    GET_CONFIG_VALUES,
    PROPOSE_CONFIG_CHANGE,
    SCAN_SERVER_ADDONS,
    GET_ADDON_DIAGNOSTICS,
    PROPOSE_ADDON_REMEDIATION,
]
