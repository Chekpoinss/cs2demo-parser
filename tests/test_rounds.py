import pytest

from cs2demo_parser.models import DemoError, Options
from cs2demo_parser.rounds import build_rounds


def start(tick, counter, warmup=False):
    return {"tick": tick, "total_rounds_played": counter, "is_warmup_period": warmup}


def end(tick, winner="CT", reason="t_killed", warmup=False):
    return {"tick": tick, "winner": winner, "reason": reason, "is_warmup_period": warmup}


def test_old_demo_bootstraps_first_round_after_warmup_without_round_start():
    events = {
        "round_start": [start(0, 0, True), start(100, 1)],
        "round_freeze_end": [start(10, 0), start(110, 1)],
        "round_end": [end(90, 2, 9), end(190, 3, 8)],
        "round_officially_ended": [{"tick": 100}],
    }
    rounds, _ = build_rounds(events, Options())
    assert [r.number for r in rounds] == [1, 2]
    assert rounds[0].start_tick == 10
    assert rounds[0].stats_end_tick == 99  # before next round's spawns
    assert [r.winner for r in rounds] == [2, 3]


def test_knife_round_is_excluded_by_last_match_start():
    events = {
        "round_start": [start(0, 0), start(100, 0)],
        "round_announce_match_start": [start(10, 0), start(110, 0)],
        "round_freeze_end": [start(10, 0), start(110, 0)],
        "round_end": [end(90), end(190, "T")],
    }
    rounds, warnings = build_rounds(events, Options())
    assert len(rounds) == 1
    assert rounds[0].start_tick == 110
    assert rounds[0].winner == 2
    assert any("ножевой" in warning for warning in warnings)


def test_backup_restore_replaces_cancelled_round_and_discards_invalid_attempts():
    events = {
        "round_start": [
            start(0, 0), start(100, 1), start(200, 2), start(220, 1), start(300, 2)
        ],
        "round_freeze_end": [start(1, 0), start(101, 1), start(230, 1), start(310, 2)],
        "round_end": [end(90), end(190), end(215, None, None), end(290, "T"), end(390)],
    }
    rounds, warnings = build_rounds(events, Options())
    assert [r.number for r in rounds] == [1, 2, 3]
    assert [r.end_tick for r in rounds] == [90, 290, 390]
    assert any("откатом" in warning for warning in warnings)


def test_unfinished_restore_does_not_keep_stale_later_rounds():
    events = {
        "round_start": [start(0, 0), start(100, 1), start(200, 2), start(300, 1)],
        "round_freeze_end": [start(1, 0), start(101, 1), start(201, 2), start(301, 1)],
        "round_end": [end(90), end(190), end(290)],
    }
    rounds, _ = build_rounds(events, Options())
    assert [r.number for r in rounds] == [1]


def test_post_round_option_and_duplicate_official_end_events():
    events = {
        "round_start": [start(0, 0), start(100, 1)],
        "round_freeze_end": [start(1, 0), start(101, 1)],
        "round_end": [end(80), end(180)],
        "round_officially_ended": [{"tick": 100}, {"tick": 100}],
        "player_death": [{"tick": 190}],
    }
    inclusive, _ = build_rounds(events, Options())
    live_only, _ = build_rounds(events, Options(include_post_round=False))
    assert [r.stats_end_tick for r in inclusive] == [99, 190]
    assert [r.stats_end_tick for r in live_only] == [80, 180]


def test_manual_exclusions_keep_original_round_numbers():
    events = {
        "round_start": [start(0, 0), start(100, 1)],
        "round_freeze_end": [start(1, 0), start(101, 1)],
        "round_end": [end(90), end(190)],
    }
    rounds, _ = build_rounds(events, Options(exclude_rounds=(1,)))
    assert [r.number for r in rounds] == [2]
    with pytest.raises(DemoError, match="нет исключаемых"):
        build_rounds(events, Options(exclude_rounds=(3,)))


def test_warmup_game_commencing_and_invalid_winners_do_not_count():
    events = {
        "round_start": [start(0, 0, True), start(100, 0), start(200, 0), start(300, 0)],
        "round_freeze_end": [start(310, 0)],
        "round_end": [end(90, warmup=True), end(190, 2, 16), end(290, None), end(390)],
    }
    rounds, _ = build_rounds(events, Options())
    assert len(rounds) == 1
    assert rounds[0].end_tick == 390


def test_no_round_end_is_an_error_not_zero_stats():
    with pytest.raises(DemoError, match="round_end"):
        build_rounds({}, Options())


def test_match_end_stops_stats_before_post_match_disconnect_and_world_deaths():
    events = {
        "round_start": [start(0, 0)],
        "round_freeze_end": [start(1, 0)],
        "round_end": [{**end(80), "is_match_started": False}],
        "player_death": [{"tick": 95}, {"tick": 150}],
    }
    rounds, _ = build_rounds(events, Options())
    assert rounds[0].stats_end_tick == 80
