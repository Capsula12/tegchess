import pytest

from tegchess.analysis.expectation import (
    confidence,
    elo_expected,
    expected_with_adjustments,
    matchup_base,
    shrunk_residual,
    wilson,
)


def test_elo_expected():
    assert elo_expected(1500, 1500) == pytest.approx(0.5)
    assert elo_expected(1800, 2200) == pytest.approx(0.0909, abs=1e-3)
    assert elo_expected(None, 1500) is None


def test_residual_is_heavily_shrunk():
    # 5 partidas ganadas de más (+0,5 cada una) casi no mueven la aguja; 200 sí.
    assert shrunk_residual(2.5, 5) < 0.08
    assert shrunk_residual(100, 200) > 0.4
    assert shrunk_residual(None, 0) == 0


def test_expected_starts_from_elo_and_adds_engine():
    flat = expected_with_adjustments(0.1)
    assert flat["expected_pct"] == 10 and flat["delta_pct"] == 0
    better = expected_with_adjustments(0.1, eval_for_me=300)
    assert better["delta_engine"] > 10 and better["expected_pct"] > 20
    # El rival que rinde de más en la posición me resta.
    worse = expected_with_adjustments(0.5, rival_residual=(30.0, 60))
    assert worse["delta_rival"] < 0
    assert 1 <= expected_with_adjustments(0.99, eval_for_me=1000)["expected_pct"] <= 99


def test_wilson_and_confidence():
    lo, hi = wilson(0.6, 10)
    assert lo < 60 < hi
    lo2, hi2 = wilson(0.6, 200)
    assert hi2 - lo2 < hi - lo
    assert wilson(None, 0) is None
    assert confidence(3) == "baja" and confidence(15) == "media" and confidence(80) == "alta"


def test_matchup_base_uses_common_perf():
    me = {"ratings": [{"perf": "blitz", "current": 1800, "games": 300}, {"perf": "rapid", "current": 1900, "games": 50}],
          "overall": {"over_pct": 1.0}}
    rival = {"ratings": [{"perf": "rapid", "current": 2200, "games": 40}], "overall": {"over_pct": -2.0}}
    base = matchup_base(me, rival)
    assert base["perf"] == "rapid" and base["gap"] == 300 and base["big_gap"]
    assert base["expected_pct"] == pytest.approx(15.1, abs=0.1)
    assert matchup_base(me, {"ratings": [], "overall": None}) is None
