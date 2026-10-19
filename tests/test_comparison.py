from tegchess.analysis.comparison import compare, opening_ranking, player_metrics
from tegchess.config import Config
from tegchess.ingest import parse_game, store_games


def _cfg(tmp_path):
    return Config.model_validate({
        "me": "german11", "opponents": ["lucky09"],
        "download": {"perf_types": ["blitz"], "max_games": None, "since": None, "rated_only": False},
        "data_dir": str(tmp_path / "data"),
    })


def test_player_metrics_and_ranking(con, tmp_path, raw_games):
    store_games(con, [parse_game(r) for r in raw_games])
    m = player_metrics(con, _cfg(tmp_path), "german11")
    assert m["games"] == len(raw_games)
    assert {"over_pct", "opening_cpl", "eval_out", "trouble_pct"} <= m.keys()
    ranking = opening_ranking(con, _cfg(tmp_path), "german11", min_games=1)
    assert all(r["rating_pts"] > 0 for r in ranking["best"])
    assert all(r["rating_pts"] < 0 for r in ranking["worst"])


def test_compare_ranks_against_rivals_with_enough_games():
    me = {"player": "yo", "games": 100, "lost_on_time_pct": 30.0, "over_pct": 2.0}
    rivals = [{"player": "a", "games": 50, "lost_on_time_pct": 10.0, "over_pct": 5.0},
              {"player": "b", "games": 60, "lost_on_time_pct": 40.0, "over_pct": -1.0},
              {"player": "c", "games": 3, "lost_on_time_pct": 0.0, "over_pct": 50.0}]   # poca muestra: no cuenta
    rows = {r["key"]: r for r in compare(me, rivals, {"a": "A", "b": "B", "c": "C"})}
    t = rows["lost_on_time_pct"]
    assert t["rivals_avg"] == 25.0 and t["best"]["name"] == "A" and t["rank"] == 2 and t["of"] == 3
    assert t["verdict"] == "peor"
    assert rows["over_pct"]["best"]["name"] == "A"
