from __future__ import annotations

from src.mc.properties_parser import parse_properties


def test_properties_parser_preserves_order_and_comments() -> None:
    doc = parse_properties("# header\nmax-players=10\n\npvp=true\n")

    doc.set("max-players", "20")

    assert doc.get("max-players") == "20"
    assert doc.to_text() == "# header\nmax-players=20\n\npvp=true\n"


def test_properties_parser_appends_missing_key_with_comment() -> None:
    doc = parse_properties("server-port=25565\n")

    doc.set("motd", "Hello")

    assert doc.get("motd") == "Hello"
    assert doc.to_text().endswith(
        "\n# Added by MC ops dashboard: motd\nmotd=Hello\n"
    )


def test_properties_parser_updates_last_duplicate_entry() -> None:
    doc = parse_properties("pvp=true\n# later override\npvp=false\n")

    doc.set("pvp", "true")

    assert doc.entry_count("pvp") == 2
    assert doc.to_text() == "pvp=true\n# later override\npvp=true\n"
