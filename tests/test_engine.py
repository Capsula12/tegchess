import chess
import pytest

from tegchess.config import find_stockfish
from tegchess.engine import EnginePool, analyse_positions, pending_positions, terminal_eval
from tegchess.ingest import parse_game, store_games

STOCKFISH = find_stockfish()


def test_terminal_eval_checkmate():
    board = chess.Board()
    for san in ["f3", "e5", "g4", "Qh4#"]:
        board.push_san(san)
    # Le toca a las blancas y están en mate => evaluación muy negativa.
    assert terminal_eval(board)["cp"] < -1000


def test_terminal_eval_stalemate():
    board = chess.Board("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1")
    assert terminal_eval(board) == {"cp": 0, "mate": None}


def test_pending_positions_skips_cached(con, raw_games):
    store_games(con, [parse_game(r) for r in raw_games])
    before = {epd for epd, _ in pending_positions(con, max_ply=10)}
    assert chess.Board().epd() in before
    # Las evaluaciones que vinieron de Lichess no quedan pendientes.
    cached = {r[0] for r in con.execute("SELECT epd FROM evals").fetchall()}
    assert not (before & cached)


@pytest.mark.skipif(STOCKFISH is None, reason="Stockfish no instalado")
def test_stockfish_finds_mate_in_one(con):
    # Mate del pastor: Qxf7#
    board = chess.Board()
    for san in ["e4", "e5", "Bc4", "Nc6", "Qh5", "Nf6"]:
        board.push_san(san)
    pool = EnginePool(STOCKFISH, workers=1)
    try:
        assert analyse_positions(con, pool, [board.epd()], depth=8) == 1
    finally:
        pool.close()
    best, mate = con.execute("SELECT best_uci, mate FROM evals").fetchone()
    assert best == "h5f7"
    assert mate == 1


def test_cloud_evals_stops_on_rate_limit():
    from tegchess.engine import cloud_evals
    from tegchess.lichess import RateLimited

    class FakeClient:
        calls = 0

        def cloud_eval(self, fen, multi_pv=1):
            self.calls += 1
            if self.calls > 2:
                raise RateLimited("429")
            return {"depth": 30, "pvs": [{"moves": "e2e4 e7e5", "cp": 20}]}

    client = FakeClient()
    epds = [chess.Board().epd()] * 5
    rows = list(cloud_evals(client, epds))
    # Corta en el primer 429 sin esperar ni reintentar: el resto queda para Stockfish.
    assert len(rows) == 2
    assert client.calls == 3
    assert rows[0]["best_uci"] == "e2e4" and rows[0]["source"] == "cloud"
