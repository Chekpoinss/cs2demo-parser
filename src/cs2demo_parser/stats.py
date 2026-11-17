"""Event-based statistics with a round-specific roster and deterministic KAST trades."""

from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from cs2demo_parser.models import DemoError, Options, Player, PlayerRound, RoundWindow
from cs2demo_parser.rating import approximate_impact, approximate_rating
from cs2demo_parser.rounds import Events
from cs2demo_parser.values import integer, steam_id, text, truth

UTILITY_WEAPONS = {"hegrenade", "inferno", "molotov", "incgrenade", "incendiary"}


def calculate_stats(
    rounds: Sequence[RoundWindow],
    events: Events,
    rosters: Mapping[int, Sequence[Player]],
    options: Options,
    end_alive: Mapping[int, Mapping[str, bool]] | None = None,
    starting_hp: Mapping[int, Mapping[str, int]] | None = None,
) -> tuple[list[dict[str, Any]], list[PlayerRound]]:
    """Count participation from each round's roster, never the match's final team list.

    end_alive is supplied by the adapter. Pure event fixtures may omit it; then
    'survived' means no recorded death. APR/KAST use damage assists by default;
    the flash-inclusive interpretation is opt-in and is recorded in the output.
    """
    combined = sorted(
        (int(event["tick"]), priority, order, name, event)
        for priority, name in enumerate(("player_hurt", "player_death"))
        for order, event in enumerate(events.get(name, []))
    )
    event_ticks = [row[0] for row in combined]
    all_rounds: list[PlayerRound] = []
    for window in rounds:
        players = {p.steamid: p for p in rosters.get(window.number, [])}
        if not players or {p.team for p in players.values()} != {2, 3}:
            raise DemoError(f"Раунд {window.number}: нет состава игроков обеих команд")
        rows = {
            sid: PlayerRound(window.number, sid, p.name, "T" if p.team == 2 else "CT")
            for sid, p in players.items()
        }
        initial_hp = starting_hp.get(window.number, {}) if starting_hp is not None else {}
        hp = {sid: max(0, initial_hp.get(sid, 100)) for sid in players}
        pending_deaths: list[tuple[int, str, str, int]] = []
        live_deaths: set[str] = set()
        opening_seen = False
        round_events = combined[
            bisect_left(event_ticks, window.start_tick):
            bisect_right(event_ticks, window.stats_end_tick)
        ]
        for tick, _, _, name, event in round_events:
            if truth(event.get("is_warmup_period")):
                continue
            in_freeze = tick < window.live_tick
            completed_rounds = integer(event.get("total_rounds_played"))
            if (
                in_freeze and window.game_round_number is not None
                and completed_rounds is not None
                and completed_rounds + 1 != window.game_round_number
            ):
                # A backup restore can kill the old pawns on the same tick as
                # the replacement round_start. Those events still carry the
                # discarded attempt's counter and must not enter the replay.
                continue
            if in_freeze and name == "player_hurt":
                # HP was sampled just before combat; applying older freeze-time
                # hurt events to that future snapshot would count damage twice.
                continue
            victim = steam_id(event.get("user_steamid"))
            attacker = steam_id(event.get("attacker_steamid"))
            if victim is None:
                if text(event.get("user_name")):
                    raise DemoError(f"Тик {tick}: игрок-жертва без SteamID64")
                # World/bot events cannot produce human-player statistics.
                continue
            if victim not in players:
                raise DemoError(
                    f"Раунд {window.number}, тик {tick}: жертвы {victim} нет в составе. "
                    "Возможен вход посреди раунда или неполная POV-запись."
                )
            if attacker is not None and attacker not in players:
                raise DemoError(f"Раунд {window.number}, тик {tick}: атакующего нет в составе")
            enemy = (
                attacker is not None
                and attacker != victim
                and players[attacker].team != players[victim].team
            )
            if name == "player_hurt":
                raw_damage = max(0, integer(event.get("dmg_health")) or 0)
                remaining = integer(event.get("health"))
                if remaining is not None:
                    # dmg_health truncates fractional damage, while health can
                    # drop by one more HP. Use actual HP loss, not floor(raw
                    # damage), and do not count damage beyond the victim's HP.
                    remaining = max(0, remaining)
                    damage = max(0, hp[victim] - remaining)
                    hp[victim] = remaining
                else:
                    damage = min(raw_damage, hp[victim])
                    hp[victim] -= damage
                if enemy:
                    rows[attacker].damage += damage
                    if text(event.get("weapon")).lower().removeprefix("weapon_") in UTILITY_WEAPONS:
                        rows[attacker].utility_damage += damage
                continue

            if not in_freeze and victim in live_deaths:
                raise DemoError(
                    f"Раунд {window.number}, тик {tick}: повторная смерть игрока. "
                    "Режим с возрождениями/дубликаты событий не поддерживается."
                )
            rows[victim].deaths += 1
            if not in_freeze:
                live_deaths.add(victim)
                hp[victim] = 0
            # A disconnect during freeze can kill the old pawn, then a
            # reconnect respawns it before combat. The game counts this death;
            # preserve it without overwriting the live-start HP snapshot.
            if not enemy:
                if attacker == victim:
                    rows[victim].suicides += 1
                elif attacker is not None:
                    rows[attacker].team_kills += 1
                continue
            rows[attacker].kills += 1
            rows[attacker].headshots += int(truth(event.get("headshot")))
            if not opening_seen:
                rows[attacker].opening_kills += 1
                rows[victim].opening_deaths += 1
                opening_seen = True
            assister = steam_id(event.get("assister_steamid"))
            if (
                assister in players
                and assister not in (attacker, victim)
                and players[assister].team == players[attacker].team
            ):
                if truth(event.get("assistedflash")):
                    rows[assister].flash_assists += 1
                else:
                    rows[assister].assists += 1
            # Killing a teammate's killer trades that teammate's death, NOT
            # the new killer's own death. All qualifying victims can be traded.
            for death_tick, dead_player, killer, dead_team in pending_deaths:
                if (
                    killer == victim
                    and dead_team == players[attacker].team
                    and 0 <= tick - death_tick <= options.trade_window * options.tickrate
                ):
                    rows[dead_player].traded = True
            pending_deaths.append((tick, victim, attacker, players[victim].team))

        for sid, row in rows.items():
            alive = True if end_alive is None else end_alive.get(window.number, {}).get(sid, False)
            row.survived = row.deaths == 0 and alive
            assists = row.assists + (row.flash_assists if options.include_flash_assists else 0)
            row.kast = bool(row.kills or assists or row.survived or row.traded)
            all_rounds.append(row)
    return aggregate_stats(all_rounds, options), all_rounds


def aggregate_stats(rows: Sequence[PlayerRound], options: Options) -> list[dict[str, Any]]:
    grouped: dict[str, list[PlayerRound]] = defaultdict(list)
    for row in rows:
        grouped[row.steamid].append(row)
    output: list[dict[str, Any]] = []
    for sid, player_rows in grouped.items():
        count = len(player_rows)
        total = {
            field: sum(getattr(row, field) for row in player_rows)
            for field in (
                "kills", "deaths", "assists", "flash_assists", "headshots", "damage",
                "utility_damage", "team_kills", "suicides", "opening_kills", "opening_deaths",
            )
        }
        counted_assists = total["assists"] + (
            total["flash_assists"] if options.include_flash_assists else 0
        )
        kpr = total["kills"] / count
        dpr = total["deaths"] / count
        apr = counted_assists / count
        adr = total["damage"] / count
        kast_rounds = sum(row.kast for row in player_rows)
        kast = 100 * kast_rounds / count
        impact = approximate_impact(kpr, apr)
        rating = approximate_rating(kast=kast, kpr=kpr, dpr=dpr, impact=impact, adr=adr)
        output.append({
            "steamid": sid,
            "name": player_rows[-1].name,
            "rounds": count,
            **total,
            "counted_assists": counted_assists,
            "kast_rounds": kast_rounds,
            "survived_rounds": sum(row.survived for row in player_rows),
            "traded_deaths": sum(row.traded for row in player_rows),
            "kpr": kpr,
            "dpr": dpr,
            "apr": apr,
            "adr": adr,
            "kast": kast,
            "impact_approx": impact,
            "rating_2_0_approx": rating,
            "headshot_percentage": (
                100 * total["headshots"] / total["kills"] if total["kills"] else 0.0
            ),
            "multikill_rounds": {
                str(kills): sum(row.kills == kills for row in player_rows) for kills in range(2, 6)
            },
        })
    return sorted(output, key=lambda player: (-player["rating_2_0_approx"], player["steamid"]))
