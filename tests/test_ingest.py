import chess

from tegchess.ingest import parse_game, store_games


def test_parse_game_basic_fields(raw_games):
    g = next(r for r in raw_games if r["id"] == "HdF6hdqD")
    parsed = parse_game(g)
    assert parsed.game["white"] == "lev_ostapenko"
    assert parsed.game["black"] == "german11"
    assert parsed.game["winner"] is None
    assert parsed.game["opening_family"] == "Rat Defense"
    assert parsed.game["num_plies"] == 19
    assert len(parsed.moves) == 19


def test_moves_chain_positions(raw_games):
    """epd_after de un ply es epd_before del siguiente, y el primero parte de la posición inicial."""
    for raw in raw_games:
        moves = parse_game(raw).moves
        assert moves[0]["epd_before"] == chess.Board().epd()
        for a, b in zip(moves, moves[1:]):
            assert a["epd_after"] == b["epd_before"]
            assert a["color"] != b["color"]


def test_clock_and_time_spent(raw_games):
    g = next(r for r in raw_games if r["id"] == "HdF6hdqD")
    moves = parse_game(g).moves
    # 2. d4 {0:02:55}: blancas tenían 3:00 tras 1.e4 => gastaron ~5s
    assert moves[2]["san"] == "d4"
    assert moves[2]["clock_cs"] // 100 == 174 or moves[2]["clock_cs"] // 100 == 175
    assert 400 <= moves[2]["spent_cs"] <= 600
    assert all(m["spent_cs"] is None or m["spent_cs"] >= 0 for m in moves)


def test_lichess_evals_are_after_move(raw_games):
    g = next(r for r in raw_games if r["id"] == "bqK4uFFF")
    parsed = parse_game(g)
    assert len(parsed.evals) == len(g["analysis"])
    assert parsed.evals[0]["epd"] == parsed.moves[0]["epd_after"]
    assert parsed.evals[0]["source"] == "lichess"


def test_store_is_idempotent(con, raw_games):
    parsed = [parse_game(r) for r in raw_games]
    store_games(con, parsed)
    store_games(con, parsed)
    assert con.execute("SELECT count(*) FROM games").fetchone()[0] == len(raw_games)
    total_plies = sum(len(p.moves) for p in parsed)
    assert con.execute("SELECT count(*) FROM moves").fetchone()[0] == total_plies
    # Cada partida aparece dos veces en player_games (una por color).
    assert con.execute("SELECT count(*) FROM player_games").fetchone()[0] == 2 * len(raw_games)
