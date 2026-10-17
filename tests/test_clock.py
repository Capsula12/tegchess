from tegchess.analysis.clock import time_hints, time_profile
from tegchess.config import Config
from tegchess.ingest import parse_game, store_games


def _cfg(tmp_path):
    return Config.model_validate({
        "me": "german11",
        "download": {"perf_types": ["blitz"], "max_games": None, "since": None, "rated_only": False},
        "data_dir": str(tmp_path / "data"),
    })


def test_time_profile(con, tmp_path, raw_games):
    store_games(con, [parse_game(r) for r in raw_games])
    tp = time_profile(con, _cfg(tmp_path), "german11")
    s = tp["summary"]
    assert s["games"] == len(raw_games)
    assert 0 <= s["trouble_games"] <= s["games"]
    assert s["lost_on_time"] <= s["losses"]
    # El reparto del tiempo por fase suma 100% en cada modalidad.
    shares = {}
    for row in tp["by_phase"]:
        shares[row["perf"]] = shares.get(row["perf"], 0) + row["share_pct"]
    assert all(abs(v - 100) < 0.5 for v in shares.values())
    assert all(0 <= c["clock_left_pct"] <= 110 for c in tp["clock_curve"])


def test_time_hints_thresholds():
    tp = {
        "summary": {"games": 50, "losses": 20, "lost_on_time": 8, "trouble_games": 20, "trouble_pct": 40.0,
                    "avg_trouble_move": 31.0, "score_in_trouble_pct": 30.0, "score_no_trouble_pct": 60.0,
                    "lost_on_time_of_losses_pct": 40.0, "avg_longest_think_pct": 5.0, "avg_longest_think_move": 15.0},
        "pressure": {"low": {"moves": 40, "error_rate_pct": 20.0}, "normal": {"moves": 500, "error_rate_pct": 8.0}},
        "thresholds": {"trouble_pct": 10, "long_think_pct": 10},
    }
    hints = time_hints(tp)
    assert len(hints) == 3
    assert "20%" in hints[0] and "8%" in hints[0]
    tp["summary"]["games"] = 5
    assert time_hints(tp) == []
