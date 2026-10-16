import chess

from tegchess.analysis.openings import opening_summary, repeated_positions, repertoire_tree
from tegchess.config import Config
from tegchess.ingest import parse_game, store_games


def _setup(con, raw_games):
    store_games(con, [parse_game(r) for r in raw_games])
    return Config.model_validate({
        "me": "german11",
        "download": {"perf_types": ["blitz"], "max_games": None, "since": None, "rated_only": False},
    })


def test_opening_summary_counts(con, raw_games):
    cfg = _setup(con, raw_games)
    rows = opening_summary(con, cfg, "german11", "black")
    expected = sum(1 for r in raw_games if r["players"]["black"]["user"]["id"] == "german11")
    assert sum(r["games"] for r in rows) == expected
    for r in rows:
        assert r["wins"] + r["draws"] + r["losses"] == r["games"]


def test_repeated_positions_detects_position_after_e4(con, raw_games):
    cfg = _setup(con, raw_games)
    board = chess.Board()
    board.push_san("e4")
    rows = repeated_positions(con, cfg, "german11", color="black", min_occurrences=3)
    after_e4 = next(r for r in rows if r["epd"] == board.epd())
    as_black_vs_e4 = sum(
        1 for r in raw_games
        if r["players"]["black"]["user"]["id"] == "german11" and r["moves"].startswith("e4 ")
    )
    assert after_e4["occurrences"] == as_black_vs_e4
    assert after_e4["color"] == "black"
    assert after_e4["usual_count"] <= after_e4["occurrences"]


def test_repertoire_tree_counts_are_consistent(con, raw_games):
    cfg = _setup(con, raw_games)
    tree = repertoire_tree(con, cfg, "german11", "white", min_count=1)
    assert tree["count"] == sum(1 for r in raw_games if r["players"]["white"]["user"]["id"] == "german11")
    assert sum(c["count"] for c in tree["children"]) == tree["count"]
