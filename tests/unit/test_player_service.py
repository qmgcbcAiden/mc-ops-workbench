from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.mc.rcon_client import RCONError
from src.service.player_service import (
    PlayerService,
    _parse_list_response,
    _parse_list_uuids_response,
    _LIST_RE,
    _UUID_ENTRY_RE,
    _JOIN_PATTERN,
    _LEAVE_PATTERN,
    _split_names,
    _new_stdout_lines,
    _make_player_entry,
    _avatar_url,
    _normalize_uuid,
    _resolve_uuid_from_usercache,
    resolve_player_uuid,
    resolve_skin_uuid,
    _empty_result,
)


class TestListRegex:
    def test_list_re_matches_standard_response(self):
        match = _LIST_RE.search("There are 2 of a max of 20 players online: Steve, Alex")
        assert match is not None
        assert match.group("online") == "2"
        assert match.group("max") == "20"
        assert match.group("players") == "Steve, Alex"

    def test_list_re_matches_singular_player(self):
        match = _LIST_RE.search("There are 1 of a max of 20 players online: Steve")
        assert match is not None
        assert match.group("online") == "1"

    def test_list_re_matches_empty(self):
        match = _LIST_RE.search("There are 0 of a max of 20 players online:")
        assert match is not None
        assert match.group("online") == "0"
        assert match.group("players") is None

    def test_list_re_matches_no_colon(self):
        match = _LIST_RE.search("There are 0 of a max of 20 players online")
        assert match is not None
        assert match.group("players") is None


class TestUUIDEntryRegex:
    def test_uuid_entry_re_single(self):
        matches = _UUID_ENTRY_RE.findall("Steve (12345678-1234-1234-1234-123456789012)")
        assert len(matches) == 1
        assert matches[0] == ("Steve", "12345678-1234-1234-1234-123456789012")

    def test_uuid_entry_re_multiple(self):
        text = "Steve (12345678-1234-1234-1234-123456789012), Alex (87654321-4321-4321-4321-210987654321)"
        matches = _UUID_ENTRY_RE.findall(text)
        assert len(matches) == 2
        assert matches[0][0] == "Steve"
        assert matches[1][0] == "Alex"


class TestJoinLeavePatterns:
    def test_join_pattern(self):
        match = _JOIN_PATTERN.search("Steve joined the game")
        assert match is not None
        assert match.group("player") == "Steve"

    def test_join_pattern_matches_prefixed_log_line(self):
        match = _JOIN_PATTERN.search("[13:05:00 INFO]: Steve joined the game")
        assert match is not None
        assert match.group("player") == "Steve"

    def test_leave_pattern(self):
        match = _LEAVE_PATTERN.search("Steve lost connection: Disconnected")
        assert match is not None
        assert match.group("player") == "Steve"

    def test_leave_pattern_matches_left_the_game(self):
        match = _LEAVE_PATTERN.search("Steve left the game")
        assert match is not None
        assert match.group("player") == "Steve"

    def test_join_pattern_with_underscore(self):
        match = _JOIN_PATTERN.search("Builder_07 joined the game")
        assert match is not None
        assert match.group("player") == "Builder_07"


class TestParseListResponse:
    def test_standard_response(self):
        players_dict, max_players = _parse_list_response(
            "There are 2 of a max of 20 players online: Steve, Alex"
        )
        assert max_players == 20
        assert len(players_dict) == 2
        assert "Steve" in players_dict
        assert "Alex" in players_dict
        assert players_dict["Steve"]["name"] == "Steve"

    def test_empty_response(self):
        players_dict, max_players = _parse_list_response(
            "There are 0 of a max of 20 players online:"
        )
        assert max_players == 20
        assert players_dict == {}

    def test_no_match(self):
        result = _parse_list_response("some random text")
        assert result == ({}, 0)


class TestParseListUUIDsResponse:
    def test_with_uuids(self):
        players_dict, max_players = _parse_list_uuids_response(
            "There are 1 of a max of 20 players online: Steve (12345678-1234-1234-1234-123456789012)"
        )
        assert max_players == 20
        assert len(players_dict) == 1
        assert players_dict["Steve"]["uuid"] == "12345678-1234-1234-1234-123456789012"
        assert players_dict["Steve"]["avatar_url"] == "https://minotar.net/helm/Steve/40.png"

    def test_with_uppercase_uuid_normalizes_for_avatar_lookup(self):
        players_dict, _max_players = _parse_list_uuids_response(
            "There are 1 of a max of 20 players online: "
            "Steve (12345678-ABCD-1234-ABCD-123456789012)"
        )
        assert players_dict["Steve"]["uuid"] == "12345678-abcd-1234-abcd-123456789012"
        assert players_dict["Steve"]["avatar_url"] == "https://minotar.net/helm/Steve/40.png"

    def test_with_compact_uuid_normalizes_for_avatar_lookup(self):
        players_dict, _max_players = _parse_list_uuids_response(
            "There are 1 of a max of 20 players online: "
            "Steve (12345678abcd1234abcd123456789012)"
        )
        assert players_dict["Steve"]["uuid"] == "12345678-abcd-1234-abcd-123456789012"

    def test_mixed_uuids_and_names(self):
        players_dict, max_players = _parse_list_uuids_response(
            "There are 2 of a max of 20 players online: "
            "Steve (12345678-1234-1234-1234-123456789012), Notch"
        )
        assert max_players == 20
        assert "Steve" in players_dict
        assert "Notch" in players_dict

    def test_no_match(self):
        result = _parse_list_uuids_response("some random text")
        assert result == ({}, 0)


class TestSplitNames:
    def test_simple(self):
        assert _split_names("Steve, Alex") == ["Steve", "Alex"]

    def test_with_spaces(self):
        assert _split_names("Steve , Alex , Builder_07") == ["Steve", "Alex", "Builder_07"]

    def test_single(self):
        assert _split_names("Steve") == ["Steve"]

    def test_empty(self):
        assert _split_names("") == []
        assert _split_names("  ") == []


class TestStdoutDiff:
    def test_initial_snapshot_returns_all_lines(self):
        assert _new_stdout_lines([], ["a", "b"]) == ["a", "b"]

    def test_appended_lines(self):
        assert _new_stdout_lines(["a", "b"], ["a", "b", "c"]) == ["c"]

    def test_ring_buffer_rotation(self):
        previous = [str(i) for i in range(1000)]
        current = [str(i) for i in range(1, 1001)]
        assert _new_stdout_lines(previous, current) == ["1000"]

    def test_reset_snapshot_treats_current_as_new(self):
        assert _new_stdout_lines(["old"], ["new"]) == ["new"]


class TestMakePlayerEntry:
    def test_with_uuid(self):
        entry = _make_player_entry("Steve", "12345678-1234-1234-1234-123456789012")
        assert entry["name"] == "Steve"
        assert entry["uuid"] == "12345678-1234-1234-1234-123456789012"
        assert entry["avatar_url"] == "https://minotar.net/helm/Steve/40.png"

    def test_without_uuid(self):
        entry = _make_player_entry("Steve")
        assert entry["name"] == "Steve"
        assert entry["uuid"] is None
        assert entry["avatar_url"] == "https://minotar.net/helm/Steve/40.png"


class TestPlayerServiceStdoutScan:
    def test_refresh_tracks_join_and_leave_from_prefixed_stdout_lines(self):
        lines = ["[13:05:00 INFO]: Steve joined the game"]
        service = PlayerService(
            player_repository=None,
            settings=SimpleNamespace(mc_rcon_password=""),
            get_server_state=lambda: "running",
            stdout_source=lambda: lines,
        )

        joined = service.refresh()
        assert joined["online_count"] == 1
        assert joined["players"][0]["name"] == "Steve"

        lines.append("[13:06:00 INFO]: Steve lost connection: Disconnected")
        left = service.refresh()
        assert left["online_count"] == 0

    def test_stdout_join_uses_uuid_resolver_for_avatar_lookup(self):
        lines = ["[13:05:00 INFO]: Aiden233 joined the game"]
        service = PlayerService(
            player_repository=None,
            settings=SimpleNamespace(mc_rcon_password=""),
            get_server_state=lambda: "running",
            stdout_source=lambda: lines,
            uuid_resolver=lambda name: (
                "12345678abcd1234abcd123456789012" if name == "Aiden233" else None
            ),
        )

        result = service.refresh()

        player = result["players"][0]
        assert player["uuid"] == "12345678-abcd-1234-abcd-123456789012"
        assert player["avatar_url"] == "https://minotar.net/helm/Aiden233/40.png"

    def test_refresh_marks_operator_from_ops_json_by_uuid(self, tmp_path):
        server_dir = tmp_path / "server"
        server_dir.mkdir()
        (server_dir / "ops.json").write_text(
            '[{"uuid": "12345678abcd1234abcd123456789012", '
            '"name": "Aiden233", "level": 4, "bypassesPlayerLimit": false}]',
            encoding="utf-8",
        )
        lines = ["[13:05:00 INFO]: Aiden233 joined the game"]
        service = PlayerService(
            player_repository=None,
            settings=SimpleNamespace(mc_rcon_password="", mc_server_dir=server_dir),
            get_server_state=lambda: "running",
            stdout_source=lambda: lines,
            uuid_resolver=lambda name: (
                "12345678-abcd-1234-abcd-123456789012" if name == "Aiden233" else None
            ),
        )

        player = service.refresh()["players"][0]

        assert player["is_operator"] is True
        assert player["operator_level"] == 4

    def test_refresh_marks_operator_from_ops_json_by_name_when_uuid_is_missing(self, tmp_path):
        server_dir = tmp_path / "server"
        server_dir.mkdir()
        (server_dir / "ops.json").write_text(
            '[{"uuid": "12345678abcd1234abcd123456789012", '
            '"name": "Steve", "level": 3, "bypassesPlayerLimit": false}]',
            encoding="utf-8",
        )
        lines = ["[13:05:00 INFO]: Steve joined the game"]
        service = PlayerService(
            player_repository=None,
            settings=SimpleNamespace(mc_rcon_password="", mc_server_dir=server_dir),
            get_server_state=lambda: "running",
            stdout_source=lambda: lines,
            uuid_resolver=lambda _name: None,
        )

        player = service.refresh()["players"][0]

        assert player["uuid"] is None
        assert player["is_operator"] is True
        assert player["operator_level"] == 3

    def test_refresh_marks_non_operator_when_ops_json_does_not_match(self, tmp_path):
        server_dir = tmp_path / "server"
        server_dir.mkdir()
        (server_dir / "ops.json").write_text(
            '[{"uuid": "12345678abcd1234abcd123456789012", '
            '"name": "Alex", "level": 4, "bypassesPlayerLimit": false}]',
            encoding="utf-8",
        )
        lines = ["[13:05:00 INFO]: Steve joined the game"]
        service = PlayerService(
            player_repository=None,
            settings=SimpleNamespace(mc_rcon_password="", mc_server_dir=server_dir),
            get_server_state=lambda: "running",
            stdout_source=lambda: lines,
            uuid_resolver=lambda _name: None,
        )

        player = service.refresh()["players"][0]

        assert player["is_operator"] is False
        assert player["operator_level"] is None

    def test_refresh_clears_player_when_stdout_only_reports_left_the_game(self):
        lines = ["[13:05:00 INFO]: Aiden233 joined the game"]
        service = PlayerService(
            player_repository=None,
            settings=SimpleNamespace(mc_rcon_password=""),
            get_server_state=lambda: "running",
            stdout_source=lambda: lines,
        )

        assert service.refresh()["online_count"] == 1

        lines.append("[13:06:00 INFO]: Aiden233 left the game")

        assert service.refresh()["online_count"] == 0

    def test_rcon_calibration_is_not_called_every_refresh(self):
        now = [0.0]
        lines: list[str] = []
        calls: list[str] = []

        def rcon_sender(_host, _port, _password, command, timeout=3.0):
            calls.append(command)
            return "There are 1 of a max of 20 players online: Steve"

        service = PlayerService(
            player_repository=None,
            settings=SimpleNamespace(
                mc_rcon_password="pw",
                mc_rcon_host="127.0.0.1",
                mc_rcon_port=25575,
            ),
            get_server_state=lambda: "running",
            stdout_source=lambda: lines,
            rcon_sender=rcon_sender,
            rcon_refresh_interval_seconds=60.0,
            clock=lambda: now[0],
        )

        first = service.refresh()
        assert first["online_count"] == 1
        assert [player["name"] for player in first["players"]] == ["Steve"]
        assert calls == ["list uuids"]

        lines.append("[13:06:00 INFO]: Alex joined the game")
        now[0] = 3.0
        second = service.refresh()

        assert calls == ["list uuids"]
        assert {player["name"] for player in second["players"]} == {"Steve", "Alex"}

    def test_rcon_list_fallback_enriches_names_with_uuid_resolver(self):
        calls: list[str] = []

        def rcon_sender(_host, _port, _password, command, timeout=3.0):
            calls.append(command)
            if command == "list uuids":
                raise RCONError("unsupported command")
            return "There are 1 of a max of 20 players online: Aiden233"

        service = PlayerService(
            player_repository=None,
            settings=SimpleNamespace(
                mc_rcon_password="pw",
                mc_rcon_host="127.0.0.1",
                mc_rcon_port=25575,
            ),
            get_server_state=lambda: "running",
            stdout_source=lambda: [],
            rcon_sender=rcon_sender,
            uuid_resolver=lambda name: (
                "12345678-abcd-1234-abcd-123456789012" if name == "Aiden233" else None
            ),
        )

        result = service.refresh()

        assert calls == ["list uuids", "list"]
        assert result["players"][0]["uuid"] == "12345678-abcd-1234-abcd-123456789012"

    def test_existing_server_uuid_uses_player_name_avatar_without_skin_lookup(self):
        server_uuid = "9662b76d-3df3-4aa5-b68f-422e8c2d54ab"

        def rcon_sender(_host, _port, _password, command, timeout=3.0):
            assert command == "list uuids"
            return f"There are 1 of a max of 20 players online: Aiden233 ({server_uuid})"

        def uuid_resolver(_name):
            raise AssertionError("avatar lookup should not require skin UUID resolution")

        service = PlayerService(
            player_repository=None,
            settings=SimpleNamespace(
                mc_rcon_password="pw",
                mc_rcon_host="127.0.0.1",
                mc_rcon_port=25575,
            ),
            get_server_state=lambda: "running",
            stdout_source=lambda: [],
            rcon_sender=rcon_sender,
            uuid_resolver=uuid_resolver,
        )

        player = service.refresh()["players"][0]

        assert player["uuid"] == server_uuid
        assert player["avatar_url"] == "https://minotar.net/helm/Aiden233/40.png"

    def test_rcon_calibration_is_skipped_while_server_is_starting(self):
        calls: list[str] = []

        def rcon_sender(_host, _port, _password, command, timeout=3.0):
            calls.append(command)
            return "There are 1 of a max of 20 players online: Steve"

        service = PlayerService(
            player_repository=None,
            settings=SimpleNamespace(
                mc_rcon_password="pw",
                mc_rcon_host="127.0.0.1",
                mc_rcon_port=25575,
            ),
            get_server_state=lambda: "starting",
            stdout_source=lambda: [],
            rcon_sender=rcon_sender,
        )

        result = service.refresh()

        assert result["online_count"] == 0
        assert calls == []

    def test_rcon_calibration_runs_again_after_interval(self):
        now = [0.0]
        calls: list[str] = []

        def rcon_sender(_host, _port, _password, command, timeout=3.0):
            calls.append(command)
            if len(calls) == 1:
                return "There are 1 of a max of 20 players online: Steve"
            return "There are 1 of a max of 20 players online: Alex"

        service = PlayerService(
            player_repository=None,
            settings=SimpleNamespace(
                mc_rcon_password="pw",
                mc_rcon_host="127.0.0.1",
                mc_rcon_port=25575,
            ),
            get_server_state=lambda: "running",
            stdout_source=lambda: [],
            rcon_sender=rcon_sender,
            rcon_refresh_interval_seconds=60.0,
            clock=lambda: now[0],
        )

        assert [player["name"] for player in service.refresh()["players"]] == ["Steve"]
        now[0] = 59.0
        assert [player["name"] for player in service.refresh()["players"]] == ["Steve"]
        now[0] = 60.0
        assert [player["name"] for player in service.refresh()["players"]] == ["Alex"]
        assert calls == ["list uuids", "list uuids"]

    def test_refresh_persists_snapshot_only_when_player_set_changes(self):
        class RepositoryStub:
            def __init__(self):
                self.snapshots = []

            def create_snapshot(self, players):
                self.snapshots.append(players)

        lines = ["[13:05:00 INFO]: Steve joined the game"]
        repository = RepositoryStub()
        service = PlayerService(
            player_repository=repository,
            settings=SimpleNamespace(mc_rcon_password=""),
            get_server_state=lambda: "running",
            stdout_source=lambda: lines,
        )

        service.refresh()
        service.refresh()
        lines.append("[13:06:00 INFO]: Alex joined the game")
        service.refresh()

        assert repository.snapshots == [["Steve"], ["Alex", "Steve"]]

    def test_player_directory_combines_cache_ops_bans_and_latest_log_ips(self, tmp_path):
        server_dir = tmp_path / "server"
        server_dir.mkdir()
        log_path = server_dir / "logs" / "latest.log"
        log_path.parent.mkdir()
        (server_dir / "usercache.json").write_text(
            '[{"name": "Steve", "uuid": "12345678abcd1234abcd123456789012"}]',
            encoding="utf-8",
        )
        (server_dir / "ops.json").write_text(
            '[{"name": "Alex", "uuid": "87654321abcd1234abcd123456789012", "level": 4}]',
            encoding="utf-8",
        )
        (server_dir / "banned-players.json").write_text(
            '[{"name": "BadGuy", "uuid": "aaaaaaaaabcd1234abcd123456789012", '
            '"reason": "griefing", "expires": "forever"}]',
            encoding="utf-8",
        )
        (server_dir / "banned-ips.json").write_text(
            '[{"ip": "10.0.0.5", "reason": "alts", "expires": "forever"}]',
            encoding="utf-8",
        )
        log_path.write_text(
            "[13:07:00 INFO]: BadGuy[/10.0.0.5:51234] logged in with entity id 4 at ([world] 0, 64, 0)\n",
            encoding="utf-8",
        )
        service = PlayerService(
            player_repository=None,
            settings=SimpleNamespace(
                mc_rcon_password="",
                mc_server_dir=server_dir,
                mc_log_path=log_path,
            ),
            get_server_state=lambda: "stopped",
            stdout_source=lambda: [],
        )

        directory = service.get_player_directory()
        players = {player["name"]: player for player in directory["players"]}

        assert set(players) == {"Alex", "BadGuy", "Steve"}
        assert players["Alex"]["is_operator"] is True
        assert players["Alex"]["operator_level"] == 4
        assert players["BadGuy"]["is_banned"] is True
        assert players["BadGuy"]["known_ips"] == ["10.0.0.5"]
        assert directory["banned_ips"][0]["players"] == ["BadGuy"]
        assert directory["counts"] == {
            "players": 3,
            "operators": 1,
            "banned_players": 1,
            "banned_ips": 1,
        }

    def test_player_directory_uses_persisted_cache_without_reading_files(self, monkeypatch):
        class RepositoryStub:
            def get_player_directory_cache(self):
                return {
                    "players": [
                        {
                            "name": "Steve",
                            "uuid": None,
                            "avatar_url": "https://minotar.net/helm/Steve/40.png",
                            "is_online": False,
                            "is_operator": True,
                            "operator_level": 4,
                            "is_banned": False,
                            "ban": None,
                            "known_ips": [],
                            "sources": ["ops"],
                        }
                    ],
                    "banned_ips": [],
                    "counts": {
                        "players": 1,
                        "operators": 1,
                        "banned_players": 0,
                        "banned_ips": 0,
                    },
                    "captured_at": "cached",
                    "ip_mapping_note": "",
                }

            def get_player_directory_source_states(self):
                return {
                    "usercache.json": {"mtime_ns": None, "size_bytes": None},
                    "ops.json": {"mtime_ns": None, "size_bytes": None},
                    "banned-players.json": {"mtime_ns": None, "size_bytes": None},
                    "banned-ips.json": {"mtime_ns": None, "size_bytes": None},
                    "latest.log": {"mtime_ns": None, "size_bytes": None},
                }

            def list_known_players(self):
                return []

        def fail_file_read(_path):
            raise AssertionError("player directory should use SQLite cache")

        monkeypatch.setattr("src.service.player_service._load_usercache_players", fail_file_read)

        service = PlayerService(
            player_repository=RepositoryStub(),
            settings=SimpleNamespace(
                mc_rcon_password="",
                mc_server_dir=None,
                mc_log_path=None,
            ),
            get_server_state=lambda: "stopped",
            stdout_source=lambda: [],
        )

        directory = service.get_player_directory()

        assert directory["players"][0]["name"] == "Steve"
        assert directory["players"][0]["is_operator"] is True

    def test_player_directory_rebuilds_cached_data_when_source_files_change(self, tmp_path):
        server_dir = tmp_path / "server"
        server_dir.mkdir()
        (server_dir / "ops.json").write_text(
            '[{"name": "Alex", "uuid": "87654321abcd1234abcd123456789012", "level": 4}]',
            encoding="utf-8",
        )

        class RepositoryStub:
            def __init__(self):
                self.replacements = []

            def get_player_directory_cache(self):
                return {
                    "players": [
                        {
                            "name": "Steve",
                            "uuid": None,
                            "avatar_url": "",
                            "is_online": False,
                            "is_operator": True,
                            "operator_level": 4,
                            "is_banned": False,
                            "ban": None,
                            "known_ips": [],
                            "sources": ["ops"],
                        }
                    ],
                    "banned_ips": [],
                    "counts": {
                        "players": 1,
                        "operators": 1,
                        "banned_players": 0,
                        "banned_ips": 0,
                    },
                    "captured_at": "cached",
                    "ip_mapping_note": "",
                }

            def get_player_directory_source_states(self):
                return {
                    "usercache.json": {"mtime_ns": None, "size_bytes": None},
                    "ops.json": {"mtime_ns": None, "size_bytes": None},
                    "banned-players.json": {"mtime_ns": None, "size_bytes": None},
                    "banned-ips.json": {"mtime_ns": None, "size_bytes": None},
                    "latest.log": {"mtime_ns": None, "size_bytes": None},
                }

            def replace_player_directory_cache(self, players, banned_ips, source_states):
                self.replacements.append({
                    "players": players,
                    "banned_ips": banned_ips,
                    "source_states": source_states,
                })

            def list_known_players(self):
                return []

        repository = RepositoryStub()
        service = PlayerService(
            player_repository=repository,
            settings=SimpleNamespace(
                mc_rcon_password="",
                mc_server_dir=server_dir,
                mc_log_path=None,
            ),
            get_server_state=lambda: "stopped",
            stdout_source=lambda: [],
        )

        directory = service.get_player_directory()

        players = {player["name"]: player for player in directory["players"]}
        assert set(players) == {"Alex"}
        assert players["Alex"]["is_operator"] is True
        assert repository.replacements

    def test_failed_rcon_calibration_is_rate_limited(self):
        now = [0.0]
        calls: list[str] = []

        def rcon_sender(_host, _port, _password, command, timeout=3.0):
            calls.append(command)
            raise OSError("connection refused")

        service = PlayerService(
            player_repository=None,
            settings=SimpleNamespace(
                mc_rcon_password="pw",
                mc_rcon_host="127.0.0.1",
                mc_rcon_port=25575,
            ),
            get_server_state=lambda: "running",
            stdout_source=lambda: [],
            rcon_sender=rcon_sender,
            rcon_refresh_interval_seconds=60.0,
            clock=lambda: now[0],
        )

        service.refresh()
        now[0] = 3.0
        service.refresh()
        now[0] = 60.0
        service.refresh()

        assert calls == ["list uuids", "list uuids"]


class TestOfflinePlayerConfigFallback:
    def test_op_updates_ops_json_when_server_is_stopped(self, tmp_path):
        server_dir = tmp_path / "server"
        server_dir.mkdir()
        (server_dir / "usercache.json").write_text(
            '[{"name": "Steve", "uuid": "12345678abcd1234abcd123456789012"}]',
            encoding="utf-8",
        )
        service = PlayerService(
            player_repository=None,
            settings=SimpleNamespace(mc_rcon_password="", mc_server_dir=server_dir),
            get_server_state=lambda: "stopped",
            stdout_source=lambda: [],
        )

        result = service.apply_offline_player_command("op Steve")

        assert result["status"] == "file_updated"
        ops = json.loads((server_dir / "ops.json").read_text(encoding="utf-8"))
        assert ops == [
            {
                "uuid": "12345678-abcd-1234-abcd-123456789012",
                "name": "Steve",
                "level": 4,
                "bypassesPlayerLimit": False,
            }
        ]

    def test_deop_removes_ops_json_entry_without_uuid_lookup(self, tmp_path):
        server_dir = tmp_path / "server"
        server_dir.mkdir()
        (server_dir / "ops.json").write_text(
            '[{"uuid":"12345678-abcd-1234-abcd-123456789012",'
            '"name":"Steve","level":4,"bypassesPlayerLimit":false}]',
            encoding="utf-8",
        )
        service = PlayerService(
            player_repository=None,
            settings=SimpleNamespace(mc_rcon_password="", mc_server_dir=server_dir),
            get_server_state=lambda: "stopped",
            stdout_source=lambda: [],
        )

        result = service.apply_offline_player_command("deop Steve")

        assert result["status"] == "file_updated"
        assert json.loads((server_dir / "ops.json").read_text(encoding="utf-8")) == []

    def test_ban_and_pardon_update_banned_players_json(self, tmp_path):
        server_dir = tmp_path / "server"
        server_dir.mkdir()
        (server_dir / "usercache.json").write_text(
            '[{"name": "Alex", "uuid": "87654321432143214321210987654321"}]',
            encoding="utf-8",
        )
        service = PlayerService(
            player_repository=None,
            settings=SimpleNamespace(mc_rcon_password="", mc_server_dir=server_dir),
            get_server_state=lambda: "stopped",
            stdout_source=lambda: [],
        )

        banned = service.apply_offline_player_command("ban Alex")
        pardoned = service.apply_offline_player_command("pardon Alex")

        assert banned["status"] == "file_updated"
        assert pardoned["status"] == "file_updated"
        assert json.loads((server_dir / "banned-players.json").read_text(encoding="utf-8")) == []

    def test_offline_op_fails_when_uuid_is_unknown(self, tmp_path):
        server_dir = tmp_path / "server"
        server_dir.mkdir()
        service = PlayerService(
            player_repository=None,
            settings=SimpleNamespace(mc_rcon_password="", mc_server_dir=server_dir),
            get_server_state=lambda: "stopped",
            stdout_source=lambda: [],
        )

        result = service.apply_offline_player_command("op Stranger")

        assert result["status"] == "failed"
        assert "UUID" in result["error_message"]
        assert not (server_dir / "ops.json").exists()

    def test_offline_config_fallback_refuses_transition_states(self, tmp_path):
        server_dir = tmp_path / "server"
        server_dir.mkdir()
        service = PlayerService(
            player_repository=None,
            settings=SimpleNamespace(mc_rcon_password="", mc_server_dir=server_dir),
            get_server_state=lambda: "starting",
            stdout_source=lambda: [],
        )

        result = service.apply_offline_player_command("ban Steve")

        assert result["status"] == "failed"
        assert "优先使用服务器命令" in result["error_message"]

    def test_offline_config_fallback_does_not_overwrite_invalid_json(self, tmp_path):
        server_dir = tmp_path / "server"
        server_dir.mkdir()
        ops_path = server_dir / "ops.json"
        ops_path.write_text("{broken", encoding="utf-8")
        service = PlayerService(
            player_repository=None,
            settings=SimpleNamespace(mc_rcon_password="", mc_server_dir=server_dir),
            get_server_state=lambda: "stopped",
            stdout_source=lambda: [],
        )

        result = service.apply_offline_player_command("deop Steve")

        assert result["status"] == "failed"
        assert "ops.json 格式无效" in result["error_message"]
        assert ops_path.read_text(encoding="utf-8") == "{broken"


class TestAvatarUrl:
    def test_with_uuid_still_uses_name_lookup(self):
        url = _avatar_url("Steve", "12345678-1234-1234-1234-123456789012")
        assert url == "https://minotar.net/helm/Steve/40.png"

    def test_without_uuid_uses_name_lookup(self):
        url = _avatar_url("Steve")
        assert url == "https://minotar.net/helm/Steve/40.png"

    def test_normalize_uuid_rejects_invalid_values(self):
        assert _normalize_uuid("uuid-123") is None
        assert _avatar_url("Steve", "uuid-123") == "https://minotar.net/helm/Steve/40.png"


class TestPlayerUuidLookup:
    def test_resolve_uuid_from_usercache_matches_name_case_insensitively(self, tmp_path):
        usercache = tmp_path / "usercache.json"
        usercache.write_text(
            '[{"name": "Aiden233", "uuid": "12345678abcd1234abcd123456789012"}]',
            encoding="utf-8",
        )

        assert _resolve_uuid_from_usercache("aiden233", tmp_path) == (
            "12345678-abcd-1234-abcd-123456789012"
        )

    def test_resolve_player_uuid_uses_usercache_before_remote_lookup(
        self,
        tmp_path,
        monkeypatch,
    ):
        usercache = tmp_path / "usercache.json"
        usercache.write_text(
            '[{"name": "Aiden233", "uuid": "12345678-abcd-1234-abcd-123456789012"}]',
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "src.service.player_service._resolve_uuid_from_mojang_profile",
            lambda name: pytest.fail(f"unexpected remote UUID lookup for {name}"),
        )

        assert resolve_player_uuid("Aiden233", SimpleNamespace(mc_server_dir=tmp_path)) == (
            "12345678-abcd-1234-abcd-123456789012"
        )

    def test_resolve_skin_uuid_falls_back_to_usercache_when_remote_is_unavailable(
        self,
        tmp_path,
        monkeypatch,
    ):
        usercache = tmp_path / "usercache.json"
        usercache.write_text(
            '[{"name": "Aiden233", "uuid": "12345678abcd1234abcd123456789012"}]',
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "src.service.player_service._resolve_uuid_from_mojang_profile",
            lambda name: None,
        )

        assert resolve_skin_uuid("Aiden233", SimpleNamespace(mc_server_dir=tmp_path)) == (
            "12345678-abcd-1234-abcd-123456789012"
        )


class TestEmptyResult:
    def test_empty_result_structure(self):
        result = _empty_result()
        assert result["online_count"] == 0
        assert result["max_players"] == 20
        assert result["players"] == []
        assert result["server_state"] == "stopped"
        assert "captured_at" in result
