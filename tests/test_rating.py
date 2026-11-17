import math

import pytest

from cs2demo_parser.rating import approximate_impact, approximate_rating


def test_user_formula_and_impact_without_intermediate_rounding():
    impact = approximate_impact(0.7, 0.2)
    assert impact == pytest.approx(1.165)
    assert approximate_rating(kast=70, kpr=0.7, dpr=0.6, impact=impact, adr=80) == pytest.approx(
        1.133668
    )


def test_kast_percentage_not_fraction():
    full = approximate_rating(kast=100, kpr=0, dpr=0, impact=-0.41, adr=0)
    one = approximate_rating(kast=1, kpr=0, dpr=0, impact=-0.41, adr=0)
    assert full - one == pytest.approx(0.0073 * 99)


@pytest.mark.parametrize("kast", [-1, 101, math.nan, math.inf])
def test_invalid_kast_is_rejected(kast):
    with pytest.raises(ValueError):
        approximate_rating(kast=kast, kpr=0, dpr=0, impact=-0.41, adr=0)


@pytest.mark.parametrize("kpr,apr", [(-1, 0), (0, -1), (math.nan, 1), (1, math.inf)])
def test_invalid_impact_inputs(kpr, apr):
    with pytest.raises(ValueError):
        approximate_impact(kpr, apr)


def test_negative_impact_is_allowed():
    assert approximate_impact(0, 0) == -0.41
