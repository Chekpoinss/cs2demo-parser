import bz2
import gzip
import json

import pandas as pd
import pytest

from cs2demo_parser.cli import main
from cs2demo_parser.export import safe_cell, write_json
from cs2demo_parser.models import Analysis, DemoError, Options
from cs2demo_parser.parser import analyze_demo, demo_file, native_call, records
from cs2demo_parser.values import steam_id


@pytest.mark.parametrize("compress,suffix", [(bz2.compress, ".bz2"), (gzip.compress, ".gz")])
def test_compressed_demo_is_streamed_to_temporary_file_and_cleaned(tmp_path, compress, suffix):
    payload = b"PBDEMS2\x00test"
    archive = tmp_path / ("match.dem" + suffix)
    archive.write_bytes(compress(payload))
    with demo_file(archive) as unpacked:
        assert unpacked.read_bytes() == payload
    assert not unpacked.exists()
    assert archive.exists()


def test_invalid_signature_is_rejected_before_native_parsing(tmp_path):
    path = tmp_path / "not-a-demo.dem"
    path.write_bytes(b"HL2DEMO\x00")
    with pytest.raises(DemoError, match="не CS2"):
        analyze_demo(path)


def test_missing_file_is_an_actionable_error(tmp_path):
    with pytest.raises(DemoError, match="не найден"):
        analyze_demo(tmp_path / "missing.dem")


def test_native_error_is_not_silently_converted_to_empty_events():
    def failing():
        raise RuntimeError("IllegalPathOp")

    with pytest.raises(DemoError, match="IllegalPathOp"):
        native_call("events", failing)


def test_native_panics_are_caught_but_keyboard_interrupts_are_not():
    class Panic(BaseException):
        pass

    def panic():
        raise Panic("native panic")

    def interrupted():
        raise KeyboardInterrupt

    with pytest.raises(DemoError, match="native panic"):
        native_call("events", panic)
    with pytest.raises(KeyboardInterrupt):
        native_call("events", interrupted)


def test_steamid64_keeps_integer_precision_and_rejects_float():
    large_id = 9007199254740993  # Above the largest integer exactly represented by a float.
    assert steam_id(large_id) == str(large_id)
    assert steam_id(str(large_id)) == str(large_id)
    with pytest.raises(DemoError, match="precision"):
        steam_id(float(large_id))


def test_json_is_unicode_and_marks_rating_as_approximation(tmp_path):
    report = Analysis("test.dem", {}, "test", Options(), [], [{"name": "Игрок"}])
    path = tmp_path / "report.json"
    write_json(report, path)
    data = json.loads(path.read_text())
    assert data["players"][0]["name"] == "Игрок"
    assert data["method"]["official_hltv"] is False
    assert data["method"]["kast_unit"] == "percent"
    with pytest.raises(FileExistsError):
        write_json(report, path)


@pytest.mark.parametrize("name", ["=1+1", " +formula", "@command", "-command"])
def test_spreadsheet_formula_names_are_escaped(name):
    assert safe_cell(name) == "'" + name
    assert safe_cell("PlayerName") == "PlayerName"
    assert safe_cell(42) == 42


def test_missing_event_legacy_empty_list_is_accepted():
    assert records([], "player_hurt", {"tick"}) == []


def test_cli_invalid_options_are_usage_errors():
    with pytest.raises(SystemExit) as error:
        main(["missing.dem", "--tickrate", "0"])
    assert error.value.code == 2


def test_cli_cannot_overwrite_source_even_with_force(tmp_path, capsys):
    path = tmp_path / "source.dem"
    path.write_bytes(b"preserve me")
    assert main([str(path), "--json", str(path), "--force"]) == 1
    assert path.read_bytes() == b"preserve me"
    assert "исходную демку" in capsys.readouterr().err


def test_cli_conflicting_destinations_are_rejected_before_parsing(tmp_path):
    output = str(tmp_path / "report.csv")
    assert main(["missing.dem", "--csv", output, "--rounds-csv", output]) == 1


@pytest.mark.parametrize("kwargs", [
    {"tickrate": -1}, {"trade_window": -1}, {"tickrate": float("nan")},
    {"trade_window": float("inf")}, {"exclude_rounds": (0,)},
])
def test_invalid_options(kwargs):
    with pytest.raises(ValueError):
        Options(**kwargs)


def test_first_live_tick_hit_uses_hp_from_before_that_ticks_events(tmp_path, monkeypatch):
    source = tmp_path / "match.dem"
    source.write_bytes(b"PBDEMS2\x00")
    events = {
        "round_start": [{"tick": 10, "total_rounds_played": 0}],
        "round_freeze_end": [{"tick": 20, "total_rounds_played": 0}],
        "round_end": [{"tick": 40, "winner": "CT", "is_match_started": False}],
        "player_hurt": [{
            "tick": 20, "attacker_steamid": "1", "user_steamid": "2",
            "dmg_health": 8, "health": 91,
        }],
    }

    class FakeParser:
        def __init__(self, path):
            pass

        def parse_header(self):
            return {"client_name": "SourceTV Demo"}

        def parse_events(self, *args, **kwargs):
            return [(name, pd.DataFrame(rows)) for name, rows in events.items()]

        def parse_ticks(self, props, ticks):
            return pd.DataFrame([
                {
                    "tick": tick, "steamid": sid, "name": sid, "team_num": team,
                    "health": 91 if sid == "2" and tick >= 20 else 100,
                    "is_alive": True, "is_connected": 0, "kills_total": 0,
                    "deaths_total": 0, "assists_total": 0, "damage_total": 0,
                }
                for tick in (19, 20, 40) if tick in ticks
                for sid, team in (("1", 2), ("2", 3))
            ])

    monkeypatch.setattr("demoparser2.DemoParser", FakeParser)
    report = analyze_demo(source)
    attacker = next(player for player in report.players if player["steamid"] == "1")
    assert attacker["damage"] == 9
