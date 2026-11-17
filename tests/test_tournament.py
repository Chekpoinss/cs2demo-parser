import bz2
from dataclasses import replace

import pytest

from cs2demo_parser.models import Analysis, DemoError, Options, PlayerRound, RoundWindow
from cs2demo_parser.rating import approximate_impact, approximate_rating
from cs2demo_parser.stats import aggregate_stats
from cs2demo_parser.tournament import (
    TournamentReport,
    analyze_tournament,
    combine_maps,
    find_demos,
    markdown_table,
    starting_side_scores,
)


def game(name, rows, options=None):
    options = options or Options()
    windows = [RoundWindow(n, n * 100, n * 100, n * 100 + 90, n * 100 + 90, 3)
               for n in sorted({row.round_number for row in rows})]
    return Analysis(
        name, {"map_name": "de_mirage"}, "test", options, windows,
        aggregate_stats(rows, options), rows,
    )


def test_tournament_rates_use_total_participation_not_unweighted_map_means():
    short = game("short.dem", [
        PlayerRound(n, "1", "Old", "CT", kills=1, deaths=1, damage=80, kast=True)
        for n in range(1, 3)
    ])
    long = game("long.dem", [
        PlayerRound(n, "1", "New", "CT", deaths=1, damage=10, kast=False)
        for n in range(1, 9)
    ])
    player = combine_maps([short, long], Options())[0]
    assert player["maps"] == 2
    assert player["rounds"] == 10
    assert player["kills"] == 2
    assert player["deaths"] == 10
    assert player["kpr"] == pytest.approx(0.2)
    assert player["dpr"] == 1
    assert player["adr"] == 24
    assert player["kast"] == 20
    expected = approximate_rating(
        kast=20, kpr=0.2, dpr=1, adr=24, impact=approximate_impact(0.2, 0)
    )
    assert player["rating_2_0_approx"] == pytest.approx(expected)
    unweighted = (short.players[0]["rating_2_0_approx"]
                  + long.players[0]["rating_2_0_approx"]) / 2
    assert player["rating_2_0_approx"] != pytest.approx(unweighted)
    assert player["name"] == "New"
    assert player["aliases"] == ["New", "Old"]
    assert player["alias_rounds"] == {"Old": 2, "New": 8}


def test_same_nick_different_steamids_are_not_merged():
    analysis = game("one.dem", [
        PlayerRound(1, "1", "Same", "CT"), PlayerRound(1, "2", "Same", "T")
    ])
    players = combine_maps([analysis], Options())
    assert {p["steamid"] for p in players} == {"1", "2"}
    assert len({p["display_name"] for p in players}) == 2


def test_headshot_percentage_is_recomputed_from_total_kills():
    first = game("first.dem", [PlayerRound(1, "1", "A", "CT", kills=1, headshots=1)])
    second = game("second.dem", [PlayerRound(1, "1", "A", "CT", kills=4, headshots=0)])
    assert combine_maps([first, second], Options())[0]["headshot_percentage"] == 20


def test_substitute_uses_only_rounds_in_which_they_participated():
    analysis = game("one.dem", [
        PlayerRound(1, "1", "A", "CT"), PlayerRound(2, "2", "B", "CT")
    ])
    assert all(p["rounds"] == 1 and p["maps"] == 1 for p in combine_maps([analysis], Options()))


def test_different_rules_cannot_be_combined():
    analysis = game("one.dem", [PlayerRound(1, "1", "A", "CT")], Options(trade_window=3))
    with pytest.raises(DemoError, match="разными правилами"):
        combine_maps([analysis], Options())


def test_find_demos_excludes_generic_archives_and_respects_recursion(tmp_path):
    (tmp_path / "a.dem").write_bytes(b"demo")
    (tmp_path / "b.dem.gz").write_bytes(b"archive")
    (tmp_path / "package.tar.gz").write_bytes(b"not a demo")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "c.dem").write_bytes(b"demo")
    assert len(find_demos(tmp_path)) == 2
    assert len(find_demos(tmp_path, recursive=True)) == 3


def test_exact_duplicates_and_compressed_copy_are_counted_once(tmp_path, monkeypatch):
    payload = b"PBDEMS2\x00sample"
    paths = [tmp_path / "a.dem", tmp_path / "b.dem", tmp_path / "c.dem.bz2"]
    paths[0].write_bytes(payload)
    paths[1].write_bytes(payload)
    paths[2].write_bytes(bz2.compress(payload))
    analysis = game("a.dem", [PlayerRound(1, "1", "A", "CT")])
    calls = []

    def parse(path, options):
        calls.append(path)
        return analysis

    monkeypatch.setattr("cs2demo_parser.tournament.analyze_demo", parse)
    report = analyze_tournament(paths)
    assert len(calls) == 1
    assert len(report.maps) == 1
    assert len(report.duplicates) == 2
    assert report.players[0]["rounds"] == 1
    assert report.to_dict()["complete"]


def test_cache_is_reused_only_for_same_engine_and_rules(tmp_path, monkeypatch):
    source = tmp_path / "one.dem"
    source.write_bytes(b"PBDEMS2\x00sample")
    cache = tmp_path / "cache"
    analysis = game("one.dem", [PlayerRound(1, "1", "A", "CT")])
    calls = []

    def parse(path, options):
        calls.append(path)
        return replace(analysis, options=options)

    monkeypatch.setattr("cs2demo_parser.tournament.analyze_demo", parse)
    monkeypatch.setattr("cs2demo_parser.tournament.engine_signature", lambda: "engine-a")
    first = analyze_tournament([source], cache_directory=cache)
    second = analyze_tournament([source], cache_directory=cache)
    assert first.players == second.players
    assert len(calls) == 1
    monkeypatch.setattr("cs2demo_parser.tournament.engine_signature", lambda: "engine-b")
    analyze_tournament([source], cache_directory=cache)
    analyze_tournament([source], Options(trade_window=3), cache_directory=cache)
    assert len(calls) == 3


def test_failed_demo_is_reported_and_does_not_hide_other_games(tmp_path, monkeypatch):
    bad, good = tmp_path / "bad.dem", tmp_path / "good.dem"
    bad.write_bytes(b"bad")
    good.write_bytes(b"good")
    analysis = game("good.dem", [PlayerRound(1, "1", "A", "CT")])

    def parse(path, options):
        if path == bad:
            raise DemoError("broken recording")
        return analysis

    monkeypatch.setattr("cs2demo_parser.tournament.analyze_demo", parse)
    report = analyze_tournament([bad, good])
    assert len(report.maps) == 1
    assert len(report.errors) == 1
    assert not report.to_dict()["complete"]
    assert report.players[0]["maps"] == 1


def test_map_scores_follow_starting_teams_through_side_switches():
    rows = [
        PlayerRound(1, "1", "A", "CT"), PlayerRound(2, "1", "A", "T"),
        PlayerRound(3, "1", "A", "CT"), PlayerRound(1, "2", "B", "T"),
        PlayerRound(2, "2", "B", "CT"), PlayerRound(3, "2", "B", "T"),
    ]
    analysis = game("one.dem", rows)
    analysis.rounds = [replace(window, winner=winner)
                       for window, winner in zip(analysis.rounds, [3, 2, 2], strict=True)]
    assert starting_side_scores(analysis) == {"CT": 2, "T": 1}


def test_empty_input_is_an_error():
    with pytest.raises(DemoError, match="ни одной"):
        analyze_tournament([])


@pytest.mark.parametrize("mismatch_field", [None, "kills", "deaths", "damage"])
def test_scoreboard_check_compares_every_basic_stat(tmp_path, monkeypatch, mismatch_field):
    source = tmp_path / "one.dem"
    source.write_bytes(b"PBDEMS2\x00sample")
    analysis = game("one.dem", [
        PlayerRound(1, "1", "A", "CT", kills=2, deaths=1, damage=150)
    ])
    analysis.scoreboard = [{
        field: analysis.players[0][field] for field in ("steamid", "kills", "deaths", "damage")
    }]
    if mismatch_field is not None:
        analysis.scoreboard[0][mismatch_field] += 1
    monkeypatch.setattr("cs2demo_parser.tournament.analyze_demo", lambda path, options: analysis)
    report = analyze_tournament([source])
    check = report.maps[0]["scoreboard_check"]
    assert check["checked_players"] == 1
    assert report.to_dict()["scoreboard_consistent"] is (mismatch_field is None)
    if mismatch_field is None:
        assert check["mismatches"] == []
    else:
        assert check["mismatches"] == [{
            "steamid": "1", "field": mismatch_field,
            "events": analysis.players[0][mismatch_field],
            "scoreboard": analysis.scoreboard[0][mismatch_field],
        }]


def test_missing_scoreboard_is_not_reported_as_verified(tmp_path, monkeypatch):
    source = tmp_path / "one.dem"
    source.write_bytes(b"PBDEMS2\x00sample")
    analysis = game("one.dem", [PlayerRound(1, "1", "A", "CT")])
    monkeypatch.setattr("cs2demo_parser.tournament.analyze_demo", lambda path, options: analysis)
    report = analyze_tournament([source])
    assert report.maps[0]["scoreboard_check"] == {"checked_players": 0, "mismatches": []}
    assert not report.to_dict()["scoreboard_consistent"]


def test_markdown_escapes_names_and_distinguishes_accounts():
    analysis = game("one.dem", [
        PlayerRound(1, "1", "Same | name\nnext", "CT"),
        PlayerRound(1, "2", "Same | name\nnext", "T"),
    ])
    report = TournamentReport([], Options(), "test", "test")
    report.players = combine_maps([analysis], Options())
    markdown = markdown_table(report)
    assert "Same \\| name next [Steam …1]" in markdown
    assert "Same \\| name next [Steam …2]" in markdown
    assert "name\nnext" not in markdown
