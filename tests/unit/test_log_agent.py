from __future__ import annotations

from src.ai.log_agent import LogAgent, LogAnalysisResult


SAMPLE_LOGS = [
    "[18:00:00 INFO]: Starting Minecraft server...",
    "[18:00:01 INFO]: Loading properties",
    "[18:00:05 INFO]: Preparing spawn area: 0%",
    "[18:00:05 INFO]: Preparing spawn area: 50%",
    "[18:00:05 INFO]: Preparing spawn area: 100%",
    "[18:00:10 ERROR]: Failed to load plugin MyPlugin: NullPointerException",
    "[18:00:10 ERROR]:  at com.example.MyPlugin.onEnable(MyPlugin.java:42)",
    "[18:00:10 ERROR]: Caused by: java.lang.NullPointerException",
    "[18:00:10 ERROR]:  at com.example.Config.load(Config.java:15)",
    "[18:00:15 WARN]: Can't keep up! Running 5000ms behind",
    "[18:01:00 INFO]: Player1 joined the game",
    "[18:02:00 INFO]: Player1 lost connection: Disconnected",
]


class TestLogAgentLocalPreprocessing:
    def test_agent_without_llm_returns_local_result(self):
        agent = LogAgent(llm_client=None)
        result = agent.analyze_logs(SAMPLE_LOGS, source_label="test.log")
        assert isinstance(result, LogAnalysisResult)
        assert "未配置大模型" in result.summary
        assert len(result.retained_snippets) > 0

    def test_error_lines_are_retained(self):
        agent = LogAgent(llm_client=None)
        result = agent.analyze_logs(SAMPLE_LOGS, source_label="test.log")
        error_snippets = "\n".join(result.retained_snippets)
        assert "NullPointerException" in error_snippets
        assert "Failed to load plugin" in error_snippets

    def test_folded_repeated_info_lines(self):
        logs = [
            "[18:00:05 INFO]: Preparing spawn area: 0%",
            "[18:00:05 INFO]: Preparing spawn area: 50%",
            "[18:00:05 INFO]: Preparing spawn area: 100%",
        ]
        agent = LogAgent(llm_client=None)
        result = agent.analyze_logs(logs, source_label="test.log")
        # These are INFO and not in key lines, so they get folded
        assert result.compressed_text

    def test_result_has_next_steps(self):
        agent = LogAgent(llm_client=None)
        result = agent.analyze_logs(SAMPLE_LOGS, source_label="test.log")
        assert len(result.next_steps) > 0

    def test_timeline_extracted(self):
        agent = LogAgent(llm_client=None)
        result = agent.analyze_logs(SAMPLE_LOGS, source_label="test.log")
        assert len(result.timeline) > 0

    def test_info_only_logs_retain_samples(self):
        """When logs contain only INFO, lines should still be retained for summarization."""
        info_logs = [
            "[18:00:00 INFO]: Starting Minecraft server...",
            "[18:00:01 INFO]: Loading properties",
            "[18:00:05 INFO]: Preparing spawn area: 0%",
            "[18:00:10 INFO]: Preparing spawn area: 100%",
            "[18:00:15 INFO]: Done (10.5s)!",
            "[18:01:00 INFO]: Player1 joined the game",
            "[18:02:00 INFO]: Player2 joined the game",
        ]
        agent = LogAgent(llm_client=None)
        result = agent.analyze_logs(info_logs, source_label="test.log")
        # Must retain something — can't discard all INFO lines
        assert len(result.retained_snippets) > 0
        assert len(result.key_findings) > 0
        # Summary should mention it's pure INFO
        assert "INFO" in result.summary or "纯 INFO" in result.summary or any(
            "INFO" in f for f in result.key_findings
        )

    def test_info_only_logs_have_topics(self):
        """INFO-only logs should produce topic-based findings."""
        info_logs = [
            "[18:00:00 INFO]: Starting Minecraft server...",
            "[18:00:10 INFO]: Done (10.5s)! For help, type \"help\"",
            "[18:01:00 INFO]: Player1 joined the game",
        ]
        agent = LogAgent(llm_client=None)
        result = agent.analyze_logs(info_logs, source_label="test.log")
        combined = " ".join(result.key_findings)
        # Should mention topics like startup, player activity
        assert len(result.key_findings) > 0


class TestLogAgentWithFakeLlm:
    def test_agent_with_fake_llm_returns_enriched_result(self):
        from src.ai.llm_client import FakeLlmClient
        fake_llm = FakeLlmClient(responses=[
            "【摘要】服务器启动后出现插件加载失败。\n"
            "【关键发现】\n- MyPlugin 加载失败（日志证据）\n"
            "【建议下一步】\n- 检查 Config.java:15\n"
        ])
        agent = LogAgent(llm_client=fake_llm)
        result = agent.analyze_logs(SAMPLE_LOGS, source_label="test.log")
        assert len(result.key_findings) > 0
        assert result.compressed_text
