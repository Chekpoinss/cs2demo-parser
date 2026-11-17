"""Command-line entry point; calculations are reusable through analyze_demo()."""

import argparse
import sys
from pathlib import Path

from cs2demo_parser import __version__
from cs2demo_parser.export import json_text, write_csv, write_json
from cs2demo_parser.models import Analysis, DemoError, Options
from cs2demo_parser.parser import analyze_demo


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Статистика CS2 демки и приближение HLTV Rating 2.0 (не официальный рейтинг)."
    )
    parser.add_argument("demo", type=Path, help="полная .dem, .dem.bz2 или .dem.gz")
    parser.add_argument("--json", metavar="PATH", help="JSON-отчёт; '-' для stdout")
    parser.add_argument("--csv", type=Path, metavar="PATH", help="статистика игроков в CSV")
    parser.add_argument(
        "--rounds-csv", type=Path, metavar="PATH", help="статистика по раундам в CSV"
    )
    parser.add_argument("--trade-window", type=float, default=5.0, metavar="SECONDS")
    parser.add_argument("--tickrate", type=float, default=64.0, help="CS2 обычно 64 тика/с")
    parser.add_argument(
        "--include-flash-assists", action="store_true", help="учитывать флеш-ассисты в APR и KAST"
    )
    parser.add_argument(
        "--no-post-round", action="store_true", help="не учитывать события после round_end"
    )
    parser.add_argument(
        "--exclude-round", type=int, action="append", default=[], metavar="N",
        help="исключить игровой раунд N; можно передать несколько раз",
    )
    parser.add_argument("--force", action="store_true", help="перезаписать существующие отчёты")
    parser.add_argument("--quiet", action="store_true", help="не печатать таблицу в консоль")
    parser.add_argument("--version", action="version", version=f"cs2stats {__version__}")
    return parser


def table(analysis: Analysis) -> str:
    rows = [["Игрок", "R", "K", "D", "A", "ADR", "KAST%", "Impact≈", "Rating2≈"]]
    for player in analysis.players:
        rows.append([
            " ".join(player["name"].split()),
            str(player["rounds"]),
            str(player["kills"]),
            str(player["deaths"]),
            str(player["counted_assists"]),
            f"{player['adr']:.1f}",
            f"{player['kast']:.1f}",
            f"{player['impact_approx']:.2f}",
            f"{player['rating_2_0_approx']:.2f}",
        ])
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    formatted = [
        "  ".join(
            value.ljust(widths[column]) if column == 0 else value.rjust(widths[column])
            for column, value in enumerate(row)
        )
        for row in rows
    ]
    formatted.insert(1, "-" * len(formatted[0]))
    return "\n".join(formatted)


def main(argv: list[str] | None = None) -> int:
    parser = argument_parser()
    args = parser.parse_args(argv)
    try:
        options = Options(
            tickrate=args.tickrate,
            trade_window=args.trade_window,
            include_flash_assists=args.include_flash_assists,
            include_post_round=not args.no_post_round,
            exclude_rounds=tuple(args.exclude_round),
        )
    except ValueError as error:
        parser.error(str(error))
    try:
        destinations = [
            Path(args.json) if args.json and args.json != "-" else None,
            args.csv, args.rounds_csv,
        ]
        resolved = [path.expanduser().resolve() for path in destinations if path is not None]
        if len(set(resolved)) != len(resolved):
            raise DemoError("Укажите разные пути для JSON, CSV и rounds-csv")
        for path in resolved:
            if path == args.demo.expanduser().resolve():
                raise DemoError("Нельзя перезаписать исходную демку отчётом")
            if path.exists() and not args.force:
                raise DemoError(f"Отчёт уже существует: {path}; используйте --force для перезаписи")
        analysis = analyze_demo(args.demo, options)
        if args.json == "-":
            print(json_text(analysis), end="")
        elif args.json:
            write_json(analysis, Path(args.json).expanduser(), force=args.force)
        if args.csv:
            write_csv(analysis, args.csv.expanduser(), force=args.force)
        if args.rounds_csv:
            write_csv(analysis, args.rounds_csv.expanduser(), per_round=True, force=args.force)
        if not args.quiet and args.json != "-":
            print(
                f"{analysis.header.get('map_name', '?')} | {len(analysis.rounds)} раундов | "
                f"demoparser2 {analysis.parser_version} | "
                f"patch {analysis.header.get('patch_version', '?')}"
            )
            print(table(analysis))
        for warning in analysis.warnings:
            print(f"Предупреждение: {warning}", file=sys.stderr)
    except (DemoError, OSError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
