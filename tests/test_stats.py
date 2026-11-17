import pytest

from cs2demo_parser.models import DemoError, Options, Player, RoundWindow
from cs2demo_parser.stats import calculate_stats

ROSTER = [Player("1", "A", 2), Player("2", "B", 2), Player("3", "C", 3), Player("4", "D", 3)]
WINDOW = RoundWindow(1, 0, 0, 100, 100, 3)


def death(tick, attacker, victim, assister=None, flash=False, **extra):
    return {
        "tick": tick, "attacker_steamid": attacker, "user_steamid": victim,
        "assister_steamid": assister, "assistedflash": flash, **extra,
    }


def hurt(tick, attacker, victim, damage, health, weapon="ak47"):
    return {
        "tick": tick, "attacker_steamid": attacker, "user_steamid": victim,
        "dmg_health": damage, "health": health, "weapon": weapon,
    }


def calculate(deaths=(), hurts=(), options=None, end_alive=None):
    players, rows = calculate_stats(
        [WINDOW], {"player_death": deaths, "player_hurt": hurts}, {1: ROSTER},
        options or Options(tickrate=1), end_alive,
    )
    return {p["steamid"]: p for p in players}, {row.steamid: row for row in rows}


@pytest.mark.parametrize("trade_tick,traded", [(15, True), (16, False)])
def test_trade_is_credited_to_the_dead_teammate_with_inclusive_boundary(trade_tick, traded):
    players, rows = calculate([death(10, "3", "1"), death(trade_tick, "2", "3")])
    assert rows["1"].traded is traded
    assert rows["1"].kast is traded
    assert players["1"]["traded_deaths"] == int(traded)
    assert not rows["2"].traded


def test_one_kill_can_trade_multiple_recent_teammate_deaths():
    roster = ROSTER + [Player("5", "E", 2)]
    _, rows = calculate_stats(
        [WINDOW], {"player_death": [
            death(10, "3", "1"), death(11, "3", "5"), death(15, "2", "3")
        ]}, {1: roster}, Options(tickrate=1),
    )
    traded = {row.steamid for row in rows if row.traded}
    assert traded == {"1", "5"}


def test_teamkill_cannot_trade_an_enemy_kill():
    players, rows = calculate([death(10, "3", "1"), death(12, "4", "3")])
    assert not rows["1"].traded
    assert players["4"]["kills"] == 0
    assert players["4"]["team_kills"] == 1
    assert players["3"]["deaths"] == 1


def test_trades_do_not_cross_round_boundaries():
    windows = [RoundWindow(1, 0, 0, 10, 10, 3), RoundWindow(2, 11, 11, 20, 20, 2)]
    _, rows = calculate_stats(
        windows, {"player_death": [death(9, "3", "1"), death(11, "2", "3")]},
        {1: ROSTER, 2: ROSTER}, Options(tickrate=1),
    )
    assert not next(row for row in rows if row.round_number == 1 and row.steamid == "1").traded


def test_overkill_is_capped_at_victims_remaining_health():
    players, _ = calculate(
        [death(2, "2", "3")], [hurt(1, "1", "3", 30, 70), hurt(2, "2", "3", 120, 0)]
    )
    assert players["1"]["damage"] == 30
    assert players["2"]["damage"] == 70
    assert sum(p["damage"] for p in players.values()) == 100


def test_actual_health_loss_handles_cs2_fractional_damage_truncation():
    players, _ = calculate(hurts=[hurt(1, "1", "3", 8, 91)])
    assert players["1"]["damage"] == 9  # floor(damage)=8, actual 100 -> 91 loss is 9


def test_starting_hp_is_taken_from_live_snapshot_not_assumed_to_be_100():
    players, _ = calculate_stats(
        [WINDOW], {"player_hurt": [hurt(1, "1", "3", 8, 51)]}, {1: ROSTER},
        Options(), starting_hp={1: {"3": 60}},
    )
    assert next(p for p in players if p["steamid"] == "1")["damage"] == 9


def test_team_and_world_damage_update_health_but_do_not_count_toward_adr():
    players, _ = calculate(hurts=[
        hurt(1, "1", "2", 40, 60), hurt(2, None, "2", 10, 50), hurt(3, "3", "2", 99, 0)
    ])
    assert players["1"]["damage"] == 0
    assert players["3"]["damage"] == 50


def test_utility_damage_is_included_in_adr_and_reported_separately():
    players, _ = calculate(hurts=[
        hurt(1, "1", "3", 40, 60, "weapon_hegrenade"), hurt(2, "1", "3", 20, 40, "inferno")
    ])
    assert players["1"]["damage"] == 60
    assert players["1"]["utility_damage"] == 60


def test_flash_assists_have_explicit_opt_in_semantics_for_apr_and_kast():
    deaths = [death(10, "2", "3", "1", True), death(20, "4", "1")]
    default, rows = calculate(deaths)
    inclusive, _ = calculate(deaths, options=Options(tickrate=1, include_flash_assists=True))
    assert default["1"]["assists"] == 0
    assert default["1"]["flash_assists"] == 1
    assert default["1"]["apr"] == 0
    assert not rows["1"].kast
    assert inclusive["1"]["apr"] == 1
    assert inclusive["1"]["kast"] == 100


def test_damage_assist_counts_in_kast_once_even_with_other_components():
    players, rows = calculate([death(10, "2", "3", "1"), death(20, "1", "4")])
    assert players["1"]["assists"] == 1
    assert players["1"]["kills"] == 1
    assert rows["1"].survived
    assert players["1"]["kast_rounds"] == 1


def test_suicide_and_world_death_count_as_deaths_not_kills():
    players, _ = calculate([death(1, "1", "1"), death(2, None, "2")])
    assert players["1"]["deaths"] == 1
    assert players["1"]["suicides"] == 1
    assert players["2"]["deaths"] == 1
    assert sum(p["kills"] for p in players.values()) == 0


def test_end_snapshot_prevents_free_survival_for_a_disconnected_player():
    _, rows = calculate(end_alive={1: {"1": False, "2": True, "3": True, "4": True}})
    assert not rows["1"].survived
    assert not rows["1"].kast
    assert rows["2"].survived


def test_participation_denominator_comes_from_each_round_roster():
    windows = [RoundWindow(1, 0, 0, 10, 10, 3), RoundWindow(2, 11, 11, 20, 20, 2)]
    rosters = {1: [ROSTER[0], ROSTER[2]], 2: [ROSTER[1], ROSTER[2]]}
    players, _ = calculate_stats(windows, {}, rosters, Options())
    by_id = {p["steamid"]: p for p in players}
    assert by_id["1"]["rounds"] == 1
    assert by_id["2"]["rounds"] == 1
    assert by_id["3"]["rounds"] == 2


def test_side_changes_use_round_roster_not_event_metadata_or_final_team():
    windows = [RoundWindow(1, 0, 0, 10, 10, 2), RoundWindow(2, 11, 11, 20, 20, 3)]
    rosters = {1: [ROSTER[0], ROSTER[2]], 2: [Player("1", "A", 3), Player("3", "C", 2)]}
    players, rows = calculate_stats(
        windows, {"player_death": [
            death(5, "1", "3", attacker_team_num=2, user_team_num=2),
            death(15, "1", "3", attacker_team_num=2, user_team_num=2),
        ]}, rosters, Options(),
    )
    assert next(p for p in players if p["steamid"] == "1")["kills"] == 2
    assert [row.side for row in rows if row.steamid == "1"] == ["T", "CT"]


def test_duplicate_deaths_are_an_error():
    with pytest.raises(DemoError, match="повторная смерть"):
        calculate([death(1, "1", "3"), death(2, "1", "3")])


def test_missing_player_in_roster_is_an_error():
    with pytest.raises(DemoError, match="нет в составе"):
        calculate([death(1, "1", "999")])


def test_warmup_and_events_outside_selected_round_are_ignored():
    players, _ = calculate([
        death(-1, "1", "3"), death(10, "1", "3", is_warmup_period=True), death(101, "1", "3")
    ])
    assert players["1"]["kills"] == 0


def test_freeze_disconnect_death_counts_without_corrupting_respawn_hp():
    window = RoundWindow(1, 0, 20, 100, 100, 3)
    players, rows = calculate_stats(
        [window], {
            "player_death": [death(5, "1", "1"), death(30, "3", "1")],
            "player_hurt": [hurt(25, "1", "3", 10, 90)],
        }, {1: ROSTER}, Options(), starting_hp={1: {"1": 100, "3": 100}},
    )
    player = next(p for p in players if p["steamid"] == "1")
    assert player["deaths"] == 2
    assert player["suicides"] == 1
    assert player["damage"] == 10
    assert not next(row for row in rows if row.steamid == "1").survived


def test_freeze_hurt_is_not_applied_to_hp_already_sampled_before_combat():
    window = RoundWindow(1, 0, 20, 100, 100, 3)
    players, _ = calculate_stats(
        [window], {"player_hurt": [hurt(5, "1", "3", 40, 60), hurt(25, "1", "3", 10, 50)]},
        {1: ROSTER}, Options(), starting_hp={1: {"3": 60}},
    )
    assert next(p for p in players if p["steamid"] == "1")["damage"] == 10


@pytest.mark.parametrize("counter,expected_deaths", [(0, 0), (1, 1), (2, 0), (None, 1)])
def test_freeze_deaths_from_discarded_restore_attempt_are_excluded(counter, expected_deaths):
    window = RoundWindow(2, 100, 120, 200, 200, 3, game_round_number=2)
    players, rows = calculate_stats(
        [window], {"player_death": [death(100, "1", "1", total_rounds_played=counter)]},
        {2: ROSTER}, Options(),
    )
    player = next(p for p in players if p["steamid"] == "1")
    assert player["deaths"] == expected_deaths
    assert player["suicides"] == expected_deaths
    assert next(row for row in rows if row.steamid == "1").survived is (expected_deaths == 0)
