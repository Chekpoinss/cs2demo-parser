"""Opt-in integration checks for a demo explicitly supplied by the user."""

import math
import os
from pathlib import Path

import pytest

from cs2demo_parser.parser import analyze_demo
from cs2demo_parser.rating import approximate_impact, approximate_rating

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def report():
    source = os.environ.get("CS2DEMO_TEST_DEMO")
    if not source:
        pytest.skip("Set CS2DEMO_TEST_DEMO to an existing local .dem; no automatic downloads")
    path = Path(source)
    assert path.is_file(), f"Explicitly requested integration fixture is missing: {path}"
    return analyze_demo(path)


def test_demo_produces_finite_statistics_from_completed_rounds(report):
    assert report.rounds
    assert len(report.players) >= 2
    assert all(row.number > 0 for row in report.rounds)
    for player in report.players:
        assert 0 <= player["kast"] <= 100
        assert 0 < player["rounds"] <= len(report.rounds)
        impact = approximate_impact(player["kpr"], player["apr"])
        expected = approximate_rating(
            kast=player["kast"], kpr=player["kpr"], dpr=player["dpr"],
            impact=impact, adr=player["adr"],
        )
        assert math.isfinite(player["rating_2_0_approx"])
        assert player["rating_2_0_approx"] == pytest.approx(expected)
        assert player["kills"] >= 0
        assert player["deaths"] >= 0
        assert player["damage"] >= 0
