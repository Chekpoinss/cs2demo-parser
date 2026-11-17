"""Batch statistics for all available tournament demos."""

import argparse
import sys
from pathlib import Path

from cs2demo_parser.export import write_json, write_rows_csv
from cs2demo_parser.models import DemoError, Options
from cs2demo_parser.tournament import analyze_tournament, find_demos, markdown_table


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Общая статистика турнира по имеющимся CS2 демкам, объединение по SteamID64."
    )
    parser.add_argument("directory", type=Path, nargs="?", default=Path("."))
    parser.add_argument("--output", type=Path, default=Path("results/tournament"))
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--trade-window", type=float, default=5.0)
    parser.add_argument("--tickrate", type=float, default=64.0)
    parser.add_argument("--include-flash-assists", action="store_true")
    parser.add_argument("--no-post-round", action="store_true")
    parser.add_argument("--force", action="store_true", help="перезаписать итоговые отчёты")
    args = parser.parse_args(argv)
    try:
        options = Options(
            tickrate=args.tickrate, trade_window=args.trade_window,
            include_flash_assists=args.include_flash_assists,
            include_post_round=not args.no_post_round,
        )
    except ValueError as error:
        parser.error(str(error))
    try:
        sources = find_demos(args.directory, recursive=args.recursive)
        output = args.output.expanduser().resolve()
        targets = [
            output / name for name in ("stats.json", "players.csv", "player_maps.csv", "players.md")
        ]
        for target in targets:
            if target.exists() and not args.force:
                raise DemoError(f"Отчёт уже существует: {target}; используйте --force")

        def progress(index: int, count: int, path: Path, state: str) -> None:
            print(f"[{index}/{count}] {path.name}: {state}", file=sys.stderr, flush=True)

        report = analyze_tournament(
            sources, options, cache_directory=output / "maps", progress=progress
        )
        write_json(report, targets[0], force=args.force)
        write_rows_csv(report.players, targets[1], force=args.force)
        write_rows_csv(report.player_maps, targets[2], force=args.force)
        with targets[3].open("w" if args.force else "x", encoding="utf-8") as handle:
            handle.write(markdown_table(report))
        print(
            f"Карт: {len(report.maps)}; игроков: {len(report.players)}; "
            f"раундов: {sum(game['rounds'] for game in report.maps)}; "
            f"дубликатов: {len(report.duplicates)}; ошибок: {len(report.errors)}"
        )
        for rank, player in enumerate(report.players, 1):
            print(
                f"{rank:2}. {player['display_name']} | {player['maps']} карт | "
                f"{player['rounds']} раундов | "
                f"{player['kills']}/{player['deaths']}/{player['counted_assists']} | "
                f"ADR {player['adr']:.1f} | KAST {player['kast']:.1f}% | "
                f"Rating≈ {player['rating_2_0_approx']:.2f}"
            )
        print(f"Отчёты: {output}")
        return 1 if report.errors else 0
    except (DemoError, OSError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
