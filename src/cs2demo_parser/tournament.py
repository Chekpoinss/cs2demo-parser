"""Combine complete maps by SteamID64, deduplicate demos and recompute weighted ratings."""

import hashlib
import importlib.metadata
import json
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cs2demo_parser.models import Analysis, DemoError, Options, PlayerRound, RoundWindow
from cs2demo_parser.parser import analyze_demo, demo_file
from cs2demo_parser.stats import aggregate_stats


@dataclass
class TournamentReport:
    source_files: list[str]
    options: Options
    parser_version: str
    engine_signature: str
    maps: list[dict[str, Any]] = field(default_factory=list)
    players: list[dict[str, Any]] = field(default_factory=list)
    player_maps: list[dict[str, Any]] = field(default_factory=list)
    duplicates: list[dict[str, str]] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    method: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "kind": "tournament",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            **asdict(self),
            "complete": not self.errors,
            "input_files_count": len(self.source_files),
            "maps_count": len(self.maps),
            "players_count": len(self.players),
            "rounds_count": sum(game["rounds"] for game in self.maps),
            "scoreboard_consistent": bool(self.maps) and all(
                game["scoreboard_check"]["checked_players"] == game["players"]
                and not game["scoreboard_check"]["mismatches"] for game in self.maps
            ),
            "aggregation": {
                "identity": "SteamID64, never nicknames",
                "display_name": "most frequent name by rounds; all aliases preserved",
                "rates": "recomputed from summed counts and player participation rounds",
                "rating": "recomputed from aggregate KAST/KPR/DPR/APR/ADR, not map mean",
                "duplicates": "identical decompressed demo bytes counted once (SHA-256)",
                "coverage": "provided demos only, not necessarily every game in the event",
            },
        }


def find_demos(directory: str | Path, *, recursive: bool = False) -> list[Path]:
    root = Path(directory).expanduser().resolve()
    if root.is_file():
        return [root]
    if not root.is_dir():
        raise DemoError(f"Папка с демками не найдена: {root}")
    candidates = root.rglob("*") if recursive else root.iterdir()
    demos = sorted(
        path for path in candidates
        if path.is_file() and path.name.lower().endswith((".dem", ".dem.gz", ".dem.bz2"))
    )
    if not demos:
        raise DemoError(f"В папке нет .dem/.dem.gz/.dem.bz2: {root}")
    return demos


def demo_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with demo_file(path) as unpacked, unpacked.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def engine_signature() -> str:
    digest = hashlib.sha256()
    for name in ("parser", "rounds", "stats", "models", "rating", "values"):
        digest.update(Path(__file__).with_name(name + ".py").read_bytes())
    return digest.hexdigest()


def restore_analysis(data: dict[str, Any], source: Path) -> Analysis:
    saved_options = dict(data["options"])
    saved_options["exclude_rounds"] = tuple(saved_options.get("exclude_rounds", []))
    return Analysis(
        demo=str(source), header=data["header"], parser_version=data["parser_version"],
        options=Options(**saved_options),
        rounds=[RoundWindow(**row) for row in data["rounds"]],
        players=data["players"],
        player_rounds=[PlayerRound(**row) for row in data["player_rounds"]],
        warnings=data["warnings"], scoreboard=data.get("scoreboard", []),
    )


def combine_maps(analyses: Sequence[Analysis], options: Options) -> list[dict[str, Any]]:
    """Pool raw player-round facts before calculating every rate and Rating 2.0."""
    if any(analysis.options != options for analysis in analyses):
        raise DemoError("Нельзя объединять карты, рассчитанные с разными правилами")
    rows = [row for analysis in analyses for row in analysis.player_rounds]
    players = aggregate_stats(rows, options)
    names: dict[str, Counter[str]] = defaultdict(Counter)
    maps: Counter[str] = Counter()
    for row in rows:
        names[row.steamid][row.name] += 1
    for analysis in analyses:
        maps.update({row.steamid for row in analysis.player_rounds})
    for player in players:
        aliases = sorted(names[player["steamid"]], key=lambda name: (
            -names[player["steamid"]][name], name
        ))
        player["name"] = aliases[0]
        player["aliases"] = aliases
        player["alias_rounds"] = dict(names[player["steamid"]])
        player["maps"] = maps[player["steamid"]]
    duplicate_names = Counter(player["name"] for player in players)
    for player in players:
        player["display_name"] = player["name"]
        if duplicate_names[player["name"]] > 1:
            player["display_name"] += f" [Steam …{player['steamid'][-5:]}]"
    return players


def markdown_table(report: TournamentReport) -> str:
    def cell(value: str) -> str:
        return " ".join(value.split()).replace("\\", "\\\\").replace("|", "\\|")

    lines = [
        "# APPK MAJOR 2025 — статистика по имеющимся демкам",
        "",
        f"Карт: **{len(report.maps)}**, игроков (SteamID): **{len(report.players)}**, "
        f"раундов: **{sum(game['rounds'] for game in report.maps)}**.",
        "",
        "| # | Игрок | Карт | Раундов | K / D / A | ADR | KAST | Rating 2.0 ≈ |",
        "|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for rank, player in enumerate(report.players, 1):
        lines.append(
            f"| {rank} | {cell(player['display_name'])} | {player['maps']} | {player['rounds']} | "
            f"{player['kills']} / {player['deaths']} / {player['counted_assists']} | "
            f"{player['adr']:.1f} | {player['kast']:.1f}% | "
            f"{player['rating_2_0_approx']:.2f} |"
        )
    lines += [
        "", "Игроки объединены по SteamID64. Разные SteamID с одинаковым ником "
        "разделены суффиксом SteamID.",
        "A — учтённые ассисты; флеш-ассисты включаются только при соответствующей опции.",
        "Rating пересчитан из суммарных показателей и раундов игрока, а не усреднён по картам.",
        "Это приближение по указанной формуле, не официальный HLTV Rating. "
        "Отрицательные значения формулы не обрезаются.",
    ]
    if report.errors:
        lines += ["", "**Отчёт неполный: есть ошибки чтения демок, см. stats.json.**"]
    return "\n".join(lines) + "\n"


def starting_side_scores(analysis: Analysis) -> dict[str, int] | None:
    """Label teams by starting side, not final CT/T side or guessed team names."""
    first_round = analysis.rounds[0].number
    winners = {row.number: row.winner for row in analysis.rounds}
    groups: dict[str, list[PlayerRound]] = defaultdict(list)
    for row in analysis.player_rounds:
        groups[row.steamid].append(row)
    scores: dict[str, int] = {}
    for side in ("CT", "T"):
        candidates = sorted(
            row.steamid for row in analysis.player_rounds
            if row.round_number == first_round and row.side == side
        )
        full_match = next(
            (groups[sid] for sid in candidates if len(groups[sid]) == len(analysis.rounds)), None
        )
        if full_match is None:
            return None
        scores[side] = sum(
            (row.side == "CT") == (winners[row.round_number] == 3) for row in full_match
        )
    return scores


def analyze_tournament(
    paths: Sequence[Path], options: Options | None = None, *,
    cache_directory: Path | None = None,
    progress: Callable[[int, int, Path, str], None] | None = None,
) -> TournamentReport:
    options = options or Options()
    sources = sorted({path.expanduser().resolve() for path in paths})
    if not sources:
        raise DemoError("Не передано ни одной демки")
    version = importlib.metadata.version("demoparser2")
    signature = engine_signature()
    option_key = json.dumps(asdict(options), sort_keys=True)
    option_digest = hashlib.sha256(option_key.encode()).hexdigest()[:12]
    report = TournamentReport([str(path) for path in sources], options, version, signature)
    analyses: list[Analysis] = []
    seen: dict[str, str] = {}
    if cache_directory is not None:
        cache_directory.mkdir(parents=True, exist_ok=True)

    for index, source in enumerate(sources, 1):
        try:
            digest = demo_digest(source)
            if digest in seen:
                report.duplicates.append({"demo": str(source), "duplicate_of": seen[digest]})
                if progress:
                    progress(index, len(sources), source, "дубликат, не учитывается")
                continue
            seen[digest] = str(source)
            cache = None if cache_directory is None else cache_directory / (
                f"{digest}_{signature[:12]}_{version}_{option_digest}.json"
            )
            if cache is not None and cache.exists():
                data = json.loads(cache.read_text(encoding="utf-8"))
                if data.get("_cache") != {
                    "demo_sha256": digest, "engine_signature": signature, "options": option_key
                }:
                    raise DemoError(f"Некорректные метаданные кеша: {cache}")
                analysis = restore_analysis(data, source)
                state = "из кеша"
            else:
                analysis = analyze_demo(source, options)
                state = "разобрана"
                if cache is not None:
                    data = analysis.to_dict()
                    data["_cache"] = {
                        "demo_sha256": digest, "engine_signature": signature, "options": option_key
                    }
                    with cache.open("x", encoding="utf-8") as handle:
                        json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
            scores = starting_side_scores(analysis)
            scoreboard = {player["steamid"]: player for player in analysis.scoreboard}
            checked_players = 0
            mismatches = []
            for player in analysis.players:
                game = scoreboard.get(player["steamid"], {})
                if not all(game.get(field) is not None for field in ("kills", "deaths", "damage")):
                    continue
                checked_players += 1
                for field in ("kills", "deaths", "damage"):
                    if player[field] != game[field]:
                        mismatches.append({
                            "steamid": player["steamid"], "field": field,
                            "events": player[field], "scoreboard": game[field],
                        })
            report.maps.append({
                "map_id": digest,
                "demo": str(source),
                "map_name": analysis.header.get("map_name"),
                "server_name": analysis.header.get("server_name"),
                "patch_version": analysis.header.get("patch_version"),
                "rounds": len(analysis.rounds),
                "players": len(analysis.players),
                "score_by_starting_side": scores,
                "scoreboard_check": {
                    "checked_players": checked_players, "mismatches": mismatches,
                },
                "warnings": analysis.warnings,
            })
            for player in analysis.players:
                report.player_maps.append({
                    "map_id": digest, "demo": source.name,
                    "map_name": analysis.header.get("map_name"), **player,
                })
            if not report.method:
                report.method = analysis.to_dict()["method"]
            analyses.append(analysis)
            if progress:
                progress(index, len(sources), source, f"{state}: {len(analysis.rounds)} раундов")
        except (DemoError, OSError, ValueError, KeyError, TypeError) as error:
            report.errors.append({"demo": str(source), "error": str(error)})
            if progress:
                progress(index, len(sources), source, f"ОШИБКА: {error}")
    report.players = combine_maps(analyses, options)
    return report
