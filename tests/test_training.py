from datetime import datetime, timezone

import chess

from tegchess.analysis.training import INTERVALS, TrainingStore, dests, exercise, repertoire_exercises, review
from tegchess.config import Config
from tegchess.ingest import parse_game, store_games


def test_dests_are_legal_moves():
    d = dests(chess.STARTING_FEN)
    assert sorted(d["e2"]) == ["e3", "e4"] and sorted(d["g1"]) == ["f3", "h3"]
    assert sum(len(v) for v in d.values()) == 20


def test_exercise_rejects_illegal_answer():
    epd = chess.Board().epd()
    assert exercise(epd, [], "white", "e2e4", "principal")["answer_san"] == "e4"
    assert exercise(epd, [], "white", "e2e5", "principal") is None


def test_review_leitner_boxes():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    c = review(None, "right", now)
    assert c["box"] == 1 and c["due"].startswith("2026-01-02")
    c = review(c, "right", now)
    assert c["box"] == 2 and c["due"].startswith("2026-01-04")
    c = review(c, "wrong", now)
    assert c["box"] == 1 and c["wrong"] == 1 and c["right"] == 2
    for _ in range(20):
        c = review(c, "right", now)
    assert c["box"] == len(INTERVALS) - 1


def test_store_keeps_modes_apart(tmp_path):
    store = TrainingStore(tmp_path / "training.json")
    store.record("lichess", "a", "right")
    store.record("pgn", "a", "wrong")
    assert store.cards("lichess")["a"]["last_result"] == "right"
    assert store.cards("pgn")["a"]["last_result"] == "wrong"


def test_repertoire_exercises_on_fixture(con, tmp_path, raw_games):
    store_games(con, [parse_game(r) for r in raw_games])
    cfg = Config.model_validate({
        "me": "german11", "data_dir": str(tmp_path / "data"), "engine": {"use_cloud_eval": False},
        "download": {"perf_types": ["blitz"], "max_games": None, "since": None, "rated_only": False},
    })
    for ex in repertoire_exercises(con, cfg, "german11"):
        board = chess.Board(ex["fen"])
        assert chess.Move.from_uci(ex["answer_uci"]) in board.legal_moves
        assert ex["line"] and ex["color"] == ("white" if board.turn else "black")
        assert ex["kind"] in ("principal", "desvio")
