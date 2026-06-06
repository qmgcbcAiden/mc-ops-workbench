from __future__ import annotations

from pathlib import Path

import pytest

from src.mc.server_log_tail import LogTailer
from src.service.log_service import parse_log_line


class TestParseLogLine:
    def test_parses_vanilla_format(self) -> None:
        result = parse_log_line("[13:04:12] [Server thread/INFO]: Starting minecraft server")
        assert result["level"] == "INFO"
        assert result["event_time"] == "13:04:12"
        assert "Starting minecraft" in result["message"]

    def test_parses_spigot_bracket_format(self) -> None:
        result = parse_log_line("[21:02:51] [ServerMain/ERROR]: Failed to load properties from file: server.properties")
        assert result["level"] == "ERROR"
        assert result["event_time"] == "21:02:51"
        assert "Failed to load properties" in result["message"]

    def test_parses_warn_with_thread(self) -> None:
        result = parse_log_line("[21:02:51] [ServerMain/WARN]: Failed to load eula.txt")
        assert result["level"] == "WARN"
        assert result["event_time"] == "21:02:51"

    def test_parses_old_sample_format(self) -> None:
        result = parse_log_line("[13:04:12 INFO]: Steve joined the game")
        assert result["level"] == "INFO"
        assert result["event_time"] == "13:04:12"
        assert "Steve joined" in result["message"]

    def test_parses_log4j_datetime_format(self) -> None:
        result = parse_log_line(
            "2026-05-20 00:03:41,778 ServerMain ERROR Unable to delete file G:\\mc\\logs\\latest.log"
        )
        assert result["level"] == "ERROR"
        assert result["event_time"] == "00:03:41"
        assert result["message"].startswith("Unable to delete file")

    @pytest.mark.parametrize(
        ("line", "expected_time", "expected_level", "expected_message"),
        [
            (
                "[275\u67082026 19:07:24.498] [C2ME Storage #8/INFO] "
                "[C2ME Storage/]: Loading chunk storage",
                "19:07:24",
                "INFO",
                "Loading chunk storage",
            ),
            (
                "[27May2026 19:07:25.002] [Server thread/WARN] "
                "[minecraft/MinecraftServer]: Can't keep up!",
                "19:07:25",
                "WARN",
                "Can't keep up!",
            ),
            (
                "[19:07:26] [INFO]: Sponge server started",
                "19:07:26",
                "INFO",
                "Sponge server started",
            ),
            (
                "19:07:27 [SEVERE] Proxy failed to bind",
                "19:07:27",
                "ERROR",
                "Proxy failed to bind",
            ),
            (
                "[main/INFO] [FabricLoader/GameProvider]: Loading Minecraft",
                None,
                "INFO",
                "Loading Minecraft",
            ),
            (
                "[13:53:46] [main/INFO] (FabricLoader/GameProvider) Loading Minecraft",
                "13:53:46",
                "INFO",
                "Loading Minecraft",
            ),
            (
                "[16:18:56] [main] [FabricLoader/GameProvider/INFO]: Loading Minecraft",
                "16:18:56",
                "INFO",
                "Loading Minecraft",
            ),
        ],
    )
    def test_parses_common_modded_and_proxy_formats(
        self,
        line: str,
        expected_time: str | None,
        expected_level: str,
        expected_message: str,
    ) -> None:
        result = parse_log_line(line)

        assert result["event_time"] == expected_time
        assert result["level"] == expected_level
        assert result["message"] == expected_message
        assert result["is_structured"] is True

    def test_forge_logger_prefix_removal_preserves_message_brackets(self) -> None:
        result = parse_log_line(
            "[27May2026 19:07:25.002] [Server thread/INFO] "
            "[minecraft/MinecraftServer]: [Not Secure] <Steve> hello"
        )

        assert result["message"] == "[Not Secure] <Steve> hello"

    def test_maps_fatal_and_debug_to_visible_levels(self) -> None:
        assert parse_log_line("[19:07:25 FATAL]: Crash")["level"] == "ERROR"
        assert parse_log_line("[19:07:25 DEBUG]: Details")["level"] == "INFO"

    def test_strips_ansi_codes(self) -> None:
        result = parse_log_line("\x1b[33m[13:04:12 WARN]: Can't keep up!\x1b[0m")
        assert result["level"] == "WARN"
        assert "Can't keep up" in result["message"]

    def test_fallback_finds_level_in_text(self) -> None:
        result = parse_log_line("Some ERROR happened at 13:04:12")
        assert result["level"] == "ERROR"

    def test_plain_line_defaults_to_info(self) -> None:
        result = parse_log_line("Some random console output")
        assert result["level"] == "INFO"
        assert result["event_time"] is None


class TestLogTailer:
    def test_reads_last_lines(self, tmp_path: Path) -> None:
        log_path = tmp_path / "latest.log"
        log_path.write_text("line1\nline2\nline3\n", encoding="utf-8")
        tailer = LogTailer(log_path)
        lines = tailer.read_last_lines(limit=2)
        assert len(lines) == 2
        assert lines[0] == "line2"
        assert lines[1] == "line3"

    def test_read_new_lines_incremental(self, tmp_path: Path) -> None:
        log_path = tmp_path / "latest.log"
        log_path.write_text("line1\n", encoding="utf-8")
        tailer = LogTailer(log_path)
        tailer.read_last_lines(limit=10)

        with log_path.open("a", encoding="utf-8") as fh:
            fh.write("line2\nline3\n")

        new_lines = tailer.read_new_lines()
        assert new_lines == ["line2", "line3"]

    def test_read_new_lines_can_limit_large_realtime_bursts(self, tmp_path: Path) -> None:
        log_path = tmp_path / "latest.log"
        log_path.write_text("line0\n", encoding="utf-8")
        tailer = LogTailer(log_path)
        tailer.read_last_lines(limit=10)

        with log_path.open("a", encoding="utf-8") as fh:
            for index in range(8):
                fh.write(f"line{index + 1}\n")

        new_lines = tailer.read_new_lines(limit=3)

        assert new_lines == ["line6", "line7", "line8"]
        assert tailer.last_skipped_count == 5

    def test_file_not_exists_returns_empty(self, tmp_path: Path) -> None:
        log_path = tmp_path / "nonexistent.log"
        tailer = LogTailer(log_path)
        assert tailer.read_last_lines() == []
        assert tailer.read_new_lines() == []

    def test_file_shrunk_resets_offset(self, tmp_path: Path) -> None:
        log_path = tmp_path / "latest.log"
        log_path.write_text("a\nb\nc\nd\ne\n", encoding="utf-8")
        tailer = LogTailer(log_path)
        tailer.read_last_lines(limit=10)

        log_path.write_text("x\ny\n", encoding="utf-8")
        new_lines = tailer.read_new_lines()
        assert "x" in new_lines

    def test_reset_clears_offset(self, tmp_path: Path) -> None:
        log_path = tmp_path / "latest.log"
        log_path.write_text("line1\n", encoding="utf-8")
        tailer = LogTailer(log_path)
        tailer.read_last_lines(limit=10)
        tailer.reset()
        assert tailer.read_new_lines() == ["line1"]

    def test_partial_line_is_buffered_until_complete(self, tmp_path: Path) -> None:
        log_path = tmp_path / "latest.log"
        log_path.write_text("", encoding="utf-8")
        tailer = LogTailer(log_path)

        log_path.write_text("partial", encoding="utf-8")
        assert tailer.read_new_lines() == []

        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(" line\nnext line\n")

        assert tailer.read_new_lines() == ["partial line", "next line"]

    def test_seek_to_end_skips_existing_lines(self, tmp_path: Path) -> None:
        log_path = tmp_path / "latest.log"
        log_path.write_text("old1\nold2\n", encoding="utf-8")
        tailer = LogTailer(log_path)

        tailer.seek_to_end()
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write("new1\n")

        assert tailer.read_new_lines() == ["new1"]

    def test_reads_from_start_when_latest_log_is_recreated_larger_than_old_offset(
        self,
        tmp_path: Path,
    ) -> None:
        log_path = tmp_path / "latest.log"
        log_path.write_text("old line\n", encoding="utf-8")
        tailer = LogTailer(log_path)
        tailer.seek_to_end()

        log_path.rename(tmp_path / "latest-old.log")
        log_path.write_text(
            "new line 1\nnew line 2\nnew line 3\nnew line 4\n",
            encoding="utf-8",
        )

        assert tailer.read_new_lines() == [
            "new line 1",
            "new line 2",
            "new line 3",
            "new line 4",
        ]

    def test_reads_from_start_when_latest_log_is_rewritten_in_place_past_old_offset(
        self,
        tmp_path: Path,
    ) -> None:
        log_path = tmp_path / "latest.log"
        log_path.write_text("old1\nold2\n", encoding="utf-8")
        tailer = LogTailer(log_path)
        tailer.seek_to_end()

        log_path.write_text(
            "new line 1\nnew line 2\nnew line 3\n",
            encoding="utf-8",
        )

        assert tailer.read_new_lines() == [
            "new line 1",
            "new line 2",
            "new line 3",
        ]
