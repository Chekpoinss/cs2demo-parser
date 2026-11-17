"""Small, parser-independent structures used by both tests and the demo adapter."""

from dataclasses import asdict, dataclass, field
from typing import Any


class DemoError(ValueError):
    """An invalid, unsupported or insufficient demo; never replaced with empty statistics."""


@dataclass(frozen=True)
class Options:
    tickrate: float = 64.0
    trade_window: float = 5.0
    include_flash_assists: bool = False
    include_post_round: bool = True
    exclude_rounds: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        import math

        if not math.isfinite(self.tickrate) or self.tickrate <= 0:
            raise ValueError("tickrate must be finite and greater than zero")
        if not math.isfinite(self.trade_window) or self.trade_window < 0:
            raise ValueError("trade_window must be finite and non-negative")
        if any(number < 1 for number in self.exclude_rounds):
            raise ValueError("excluded round numbers must be positive")


@dataclass(frozen=True)
class RoundWindow:
    number: int
    start_tick: int
    live_tick: int
    end_tick: int
    stats_end_tick: int
    winner: int
    reason: int | str | None = None
    game_round_number: int | None = None


@dataclass(frozen=True)
class Player:
    steamid: str
    name: str
    team: int


@dataclass
class PlayerRound:
    round_number: int
    steamid: str
    name: str
    side: str
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    flash_assists: int = 0
    headshots: int = 0
    damage: int = 0
    utility_damage: int = 0
    team_kills: int = 0
    suicides: int = 0
    opening_kills: int = 0
    opening_deaths: int = 0
    traded: bool = False
    survived: bool = False
    kast: bool = False


@dataclass
class Analysis:
    demo: str
    header: dict[str, Any]
    parser_version: str
    options: Options
    rounds: list[RoundWindow]
    players: list[dict[str, Any]]
    player_rounds: list[PlayerRound] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    scoreboard: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["schema_version"] = 1
        data["method"] = {
            "rating": "hltv_2_0_approx",
            "official_hltv": False,
            "rating_formula": (
                "0.0073*KAST + 0.3591*KPR - 0.5329*DPR + "
                "0.2372*Impact + 0.0032*ADR + 0.1587"
            ),
            "impact_formula": "2.13*KPR + 0.42*APR - 0.41",
            "kast_unit": "percent",
            "assists": (
                "game-credited damage and flash assists"
                if self.options.include_flash_assists
                else "game-credited non-flash assists only"
            ),
            "damage": "actual enemy HP loss from health deltas, excluding overkill",
            "rounds": "completed non-warmup rounds, including overtime",
        }
        return data
