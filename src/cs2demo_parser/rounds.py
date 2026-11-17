"""Round boundaries, warmup/knife exclusion and server restore/rollback handling."""

from bisect import bisect_left, bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from cs2demo_parser.models import DemoError, Options, RoundWindow
from cs2demo_parser.values import integer, team_number, text, truth

Event = Mapping[str, Any]
Events = Mapping[str, Sequence[Event]]


def build_rounds(events: Events, options: Options) -> tuple[list[RoundWindow], list[str]]:
    """Use start-state counters, NOT round_end count or its synthetic 'round' column.

    Newer demos may return 'CT'/'T' winners and text reasons; older ones use numbers.
    On a backup restore, remove ALL previously completed rounds being rolled back,
    even if the replacement attempt is unfinished at the end of the recording.
    """
    warnings: list[str] = []
    ends = sorted(events.get("round_end", []), key=lambda e: int(e["tick"]))
    if not ends:
        raise DemoError("В демке нет round_end: нет завершённых раундов для расчёта рейтинга")
    freezes = sorted(events.get("round_freeze_end", []), key=lambda e: int(e["tick"]))
    officials = sorted({int(e["tick"]) for e in events.get("round_officially_ended", [])})
    starts = {int(e["tick"]): e for e in events.get("round_start", [])}
    announcements = [
        e for e in events.get("round_announce_match_start", [])
        if not truth(e.get("is_warmup_period"))
    ]
    if announcements:
        last_announcement = max(announcements, key=lambda e: int(e["tick"]))
        first_tick = int(last_announcement["tick"])
        # The first competitive round sometimes has NO non-warmup round_start.
        starts[first_tick] = last_announcement
        starts = {tick: event for tick, event in starts.items() if tick >= first_tick}
        before_match = sum(int(e["tick"]) < first_tick for e in ends)
        if before_match:
            warnings.append(
                f"Исключено раундов до последнего старта матча: {before_match} "
                "(ножевой раунд/предматчевые перезапуски)."
            )
    if not starts:
        starts = {int(e["tick"]): e for e in freezes}
        warnings.append("Нет round_start; начало раундов восстановлено по round_freeze_end.")
    if not starts:
        raise DemoError("Нет событий начала раунда: полноценная статистика недоступна")

    end_ticks = [int(e["tick"]) for e in ends]
    freeze_ticks = [int(e["tick"]) for e in freezes]
    combat_ticks = sorted(
        int(e["tick"])
        for name in ("player_death", "player_hurt")
        for e in events.get(name, [])
        if not truth(e.get("is_warmup_period"))
    )
    anchors = sorted(starts.items())
    accepted: list[RoundWindow] = []
    discarded = 0
    unfinished = 0

    for index, (start_tick, start_event) in enumerate(anchors):
        next_tick = anchors[index + 1][0] if index + 1 < len(anchors) else 2**63 - 1
        round_ends = ends[bisect_left(end_ticks, start_tick):bisect_left(end_ticks, next_tick)]
        valid_ends = [
            e for e in round_ends
            if not truth(e.get("is_warmup_period"))
            and team_number(e.get("winner")) is not None
            and text(e.get("reason")).lower() not in {"16", "game_start", "game_commencing"}
        ]
        round_end = valid_ends[0] if valid_ends else None
        end_tick = int(round_end["tick"]) if round_end else next_tick - 1
        round_freezes = [
            e for e in freezes[
                bisect_left(freeze_ticks, start_tick):bisect_right(freeze_ticks, end_tick)
            ]
            if not truth(e.get("is_warmup_period"))
        ]
        freeze = round_freezes[0] if round_freezes else None
        if truth(start_event.get("is_warmup_period")):
            if freeze is None:
                continue
            start_tick = int(freeze["tick"])
            start_event = freeze
        live_tick = int(freeze["tick"]) if freeze else start_tick
        counter = integer(start_event.get("total_rounds_played"))
        if freeze is not None:
            freeze_counter = integer(freeze.get("total_rounds_played"))
            if freeze_counter is not None:
                counter = freeze_counter
        game_number = counter + 1 if counter is not None else None

        if game_number is not None:
            kept = [
                r for r in accepted
                if r.game_round_number is None or r.game_round_number < game_number
            ]
            discarded += len(accepted) - len(kept)
            accepted = kept
        if round_end is None:
            unfinished += 1
            continue
        if not freeze:
            warnings.append(f"Раунд {game_number or len(accepted) + 1}: нет round_freeze_end.")

        stats_end = end_tick
        # A final match-end may leave the server running for minutes. Players
        # disconnect/fall/die afterwards; these are NOT last-round statistics.
        match_finished = (
            round_end.get("is_match_started") is not None
            and not truth(round_end["is_match_started"])
        )
        if options.include_post_round and not match_finished:
            official_index = bisect_left(officials, end_tick)
            if official_index < len(officials) and officials[official_index] <= next_tick:
                stats_end = min(officials[official_index], next_tick - 1)
            elif next_tick != 2**63 - 1:
                stats_end = next_tick - 1
            else:
                last_combat = bisect_left(combat_ticks, next_tick) - 1
                if last_combat >= 0:
                    stats_end = max(end_tick, combat_ticks[last_combat])
        raw_reason = round_end.get("reason")
        reason = integer(raw_reason)
        if reason is None:
            reason = text(raw_reason) or None
        accepted.append(RoundWindow(
            number=game_number or len(accepted) + 1,
            start_tick=start_tick,
            live_tick=live_tick,
            end_tick=end_tick,
            stats_end_tick=stats_end,
            winner=team_number(round_end["winner"]),
            reason=reason,
            game_round_number=game_number,
        ))

    if discarded:
        warnings.append(f"Исключено завершённых раундов, отменённых откатом сервера: {discarded}.")
    if unfinished:
        warnings.append(f"Исключено незавершённых/перезапущенных попыток раунда: {unfinished}.")
    if any(r.game_round_number is None for r in accepted):
        warnings.append("Нет счётчика раундов; нельзя достоверно распознать откаты сервера.")
        accepted = [replace(r, number=i) for i, r in enumerate(accepted, 1)]
    numbers = [r.number for r in accepted]
    if numbers and numbers != list(range(1, max(numbers) + 1)):
        warnings.append("В записи есть пропуски номеров раундов; статистика только по записанным.")
    unknown_exclusions = set(options.exclude_rounds) - set(numbers)
    if unknown_exclusions:
        raise DemoError(f"В матче нет исключаемых раундов: {sorted(unknown_exclusions)}")
    accepted = [r for r in accepted if r.number not in options.exclude_rounds]
    if not accepted:
        raise DemoError("Не осталось завершённых игровых раундов для расчёта рейтинга")
    return accepted, warnings
