"""Thin demoparser2 adapter. No hand-written Source 1/Source 2 binary decoder."""

import bz2
import gzip
import importlib.metadata
import tempfile
from collections import defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar

from cs2demo_parser.models import Analysis, DemoError, Options, Player
from cs2demo_parser.rounds import build_rounds
from cs2demo_parser.stats import calculate_stats
from cs2demo_parser.values import integer, steam_id, team_number, text, truth

EVENT_NAMES = [
    "round_start", "round_end", "round_freeze_end", "round_officially_ended",
    "round_announce_match_start", "player_death", "player_hurt",
]
SNAPSHOT_PROPS = [
    "team_num", "is_alive", "is_connected", "health", "kills_total", "deaths_total",
    "assists_total", "damage_total",
]
T = TypeVar("T")


def native_call(label: str, call: Callable[[], T]) -> T:
    """Convert native errors (including PyO3 panics) into actionable failures.

    Never suppress errors with an empty DataFrame/zero-match fallback. Interrupts
    and process exits still propagate normally.
    """
    try:
        return call()
    except BaseException as error:
        if isinstance(error, (KeyboardInterrupt, SystemExit, GeneratorExit)):
            raise
        raise DemoError(
            f"demoparser2: ошибка {label}: {error}. "
            "Проверьте целостность демки и совместимость версии demoparser2."
        ) from error


@contextmanager
def demo_file(path: Path) -> Iterator[Path]:
    if not path.is_file():
        raise DemoError(f"Файл демки не найден: {path}")
    if path.suffix.lower() not in {".gz", ".bz2"}:
        yield path
        return
    temp_root = Path("/tmp/opencode")
    with tempfile.TemporaryDirectory(
        prefix="cs2stats-", dir=temp_root if temp_root.is_dir() else None
    ) as directory:
        unpacked = Path(directory) / "demo.dem"
        opener = bz2.open if path.suffix.lower() == ".bz2" else gzip.open
        try:
            with opener(path, "rb") as source, unpacked.open("wb") as destination:
                while block := source.read(1024 * 1024):
                    destination.write(block)
        except (OSError, EOFError) as error:
            raise DemoError(f"Не удалось распаковать {path.name}: {error}") from error
        yield unpacked


def records(frame: Any, name: str, required: set[str]) -> list[dict[str, Any]]:
    # Some older binding versions returned [] for events absent in a demo.
    if isinstance(frame, list) and not frame:
        return []
    if not hasattr(frame, "columns") or not hasattr(frame, "to_dict"):
        raise DemoError(f"demoparser2 вернул неожиданный формат {name}")
    if len(frame) == 0:
        return []
    missing = required - set(frame.columns)
    if missing:
        raise DemoError(f"В {name} отсутствуют необходимые поля: {sorted(missing)}")
    return frame.to_dict(orient="records")


def analyze_demo(path: str | Path, options: Options | None = None) -> Analysis:
    """Read one complete CS2 GOTV/SourceTV demo or a .dem.bz2/.dem.gz archive."""
    from demoparser2 import DemoParser

    source = Path(path).expanduser().resolve()
    options = options or Options()
    parser_version = importlib.metadata.version("demoparser2")
    with demo_file(source) as unpacked:
        with unpacked.open("rb") as handle:
            if handle.read(8) != b"PBDEMS2\x00":
                raise DemoError(
                    "Это не CS2/Source 2 демка (ожидалась сигнатура PBDEMS2). "
                    "Архивы .rar/.zip/.zst сначала распакуйте; CS:GO не поддерживается."
                )
        parser = native_call("открытия демки", lambda: DemoParser(str(unpacked)))
        header = native_call("чтения заголовка", parser.parse_header)
        client = text(header.get("client_name")).lower()
        if client and not any(marker in client for marker in ("sourcetv", "gotv", "hltv")):
            raise DemoError(
                "Нужна полная GOTV/SourceTV демка: POV-запись может не содержать "
                "событий и состояния всех игроков."
            )
        parsed_events = native_call("чтения событий", lambda: parser.parse_events(
            EVENT_NAMES,
            player=["team_num"],
            other=["is_warmup_period", "total_rounds_played", "is_match_started"],
        ))
        events: dict[str, list[dict[str, Any]]] = {}
        for name, frame in parsed_events:
            required = {"tick"}
            if name == "player_death":
                required |= {"user_steamid", "attacker_steamid", "assister_steamid"}
            elif name == "player_hurt":
                required |= {"user_steamid", "attacker_steamid", "dmg_health", "health"}
            elif name == "round_end":
                required |= {"winner"}
            events[name] = records(frame, name, required)
        rounds, warnings = build_rounds(events, options)
        wanted_ticks: set[int] = set()
        for window in rounds:
            wanted_ticks.update((
                window.live_tick,
                max(0, window.live_tick - 1),
                min(window.end_tick, window.live_tick + 1),
                window.end_tick,
                window.stats_end_tick,
                max(window.end_tick, window.stats_end_tick - 1),
            ))
        snapshot_frame = native_call("чтения состава/состояния игроков", lambda: parser.parse_ticks(
            SNAPSHOT_PROPS, ticks=sorted(wanted_ticks)
        ))
        snapshot_rows = records(
            snapshot_frame, "parse_ticks", {"tick", "steamid", "name", *SNAPSHOT_PROPS}
        )
        snapshots: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in snapshot_rows:
            snapshots[int(row["tick"])].append(row)
        rosters: dict[int, list[Player]] = {}
        end_alive: dict[int, dict[str, bool]] = {}
        starting_hp: dict[int, dict[str, int]] = {}
        for window in rounds:
            start_samples = (
                window.live_tick,
                max(0, window.live_tick - 1),
                min(window.end_tick, window.live_tick + 1),
            )
            start_rows = next((snapshots[tick] for tick in start_samples if snapshots[tick]), [])
            before_combat = {
                steam_id(row.get("steamid")): row
                for row in snapshots[max(0, window.live_tick - 1)]
            }
            roster = []
            starting_hp[window.number] = {}
            for row in start_rows:
                sid = steam_id(row.get("steamid"))
                team = team_number(row.get("team_num"))
                if sid is not None and team is not None:
                    roster.append(Player(sid, text(row.get("name"), sid), team))
                    previous = before_combat.get(sid, {})
                    # A tick snapshot is taken AFTER that tick's events. Prefer
                    # HP from just before combat, so a hit on the first live tick
                    # is not mistaken for already-lost starting health.
                    hp_row = (
                        previous if truth(previous.get("is_alive"))
                        and team_number(previous.get("team_num")) == team else row
                    )
                    health = integer(hp_row.get("health"))
                    if health is None:
                        raise DemoError(f"Раунд {window.number}: нет стартового HP игрока {sid}")
                    starting_hp[window.number][sid] = health
            rosters[window.number] = roster
            if len(roster) != 10:
                warnings.append(
                    f"Раунд {window.number}: в составе {len(roster)} игроков вместо 10; "
                    "проверьте отключения/режим игры."
                )
            end_samples = (
                window.stats_end_tick,
                max(window.end_tick, window.stats_end_tick - 1),
                window.end_tick,
            )
            end_rows = next((snapshots[tick] for tick in end_samples if snapshots[tick]), [])
            if not end_rows:
                raise DemoError(f"Раунд {window.number}: нет состояния игроков в конце раунда")
            end_alive[window.number] = {
                sid: truth(row.get("is_alive")) and integer(row.get("is_connected")) == 0
                for row in end_rows
                if (sid := steam_id(row.get("steamid"))) is not None
            }
        players, player_rounds = calculate_stats(
            rounds, events, rosters, options, end_alive, starting_hp
        )
        scoreboard_rows = snapshots[rounds[-1].end_tick]
        scoreboard = [
            {
                "steamid": sid,
                "name": text(row.get("name"), sid),
                "kills": integer(row.get("kills_total")),
                "deaths": integer(row.get("deaths_total")),
                "assists": integer(row.get("assists_total")),
                "damage": integer(row.get("damage_total")),
            }
            for row in scoreboard_rows
            if (sid := steam_id(row.get("steamid"))) is not None
        ]
        if not options.exclude_rounds:
            scoreboard_by_id = {row["steamid"]: row for row in scoreboard}
            for player in players:
                game = scoreboard_by_id.get(player["steamid"], {})
                for field in ("kills", "deaths"):
                    if game.get(field) is not None and player[field] != game[field]:
                        warnings.append(
                            f"{player['name']}: {field} по событиям {player[field]}, "
                            f"в таблице игры {game[field]}. Проверьте откаты, "
                            "самоубийства/teamkill и полноту записи."
                        )
    return Analysis(
        demo=str(source),
        header=header,
        parser_version=parser_version,
        options=options,
        rounds=rounds,
        players=players,
        player_rounds=player_rounds,
        warnings=list(dict.fromkeys(warnings)),
        scoreboard=scoreboard,
    )
