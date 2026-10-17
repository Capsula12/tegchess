import chess
import pytest

from tegchess.analysis.crossover import battle_positions, family_matchup
from tegchess.analysis.traps import find_traps
from tegchess.config import Config, find_stockfish
from tegchess.engine import evaluate_pending
from tegchess.ingest import parse_game, store_games


def _cfg(tmp_path, **analysis):
    return Config.model_validate({
        "me": "german11",
        "opponents": ["lucky09"],
        "download": {"perf_types": ["blitz"], "max_games": None, "since": None, "rated_only": False},
        "engine": {"depth": 8, "workers": 2, "use_cloud_eval": False, "max_ply": 20},
        "analysis": {"min_occurrences": 3, **analysis},
        "data_dir": str(tmp_path / "data"),
    })


def test_battle_positions_finds_shared_positions(con, tmp_path, raw_games):
    store_games(con, [parse_game(r) for r in raw_games])
    cfg = _cfg(tmp_path)
    # german11 con negras vs lucky09 con blancas: comparten la partida YXdfoPok.
    battles = battle_positions(con, cfg, "german11", "lucky09", "black", min_games=1)
    start = chess.Board().epd()
    assert any(b["epd"] == start for b in battles)
    for b in battles:
        assert 0 <= b["expected_pct"] <= 100
        assert b["to_move"] in ("me", "rival")
        assert b["confidence"] in ("baja", "media", "alta")
    fams = family_matchup(con, cfg, "german11", "lucky09", "black")
    assert any(f["opening"] == "Philidor Defense" for f in fams)


def _fake_game(gid: str, sans: list[str], white: str, black: str, winner: str | None) -> dict:
    return {
        "id": gid, "rated": False, "variant": "standard", "speed": "blitz", "perf": "blitz",
        "createdAt": 1790000000000 + int(gid[-2:]) * 1000, "status": "mate" if winner else "draw",
        "winner": winner,
        "players": {"white": {"user": {"name": white, "id": white.lower()}, "rating": 1500},
                    "black": {"user": {"name": black, "id": black.lower()}, "rating": 1500}},
        "moves": " ".join(sans),
    }


@pytest.mark.skipif(find_stockfish() is None, reason="Stockfish no instalado")
def test_find_traps_detects_scholars_mate(con, tmp_path):
    # El rival (negras) cae siempre en 3...Nf6?? frente a Qh5 + Bc4: la refutación es 4.Qxf7#.
    trap = ["e4", "e5", "Qh5", "Nc6", "Bc4", "Nf6", "Qxf7#"]
    games = [_fake_game(f"g{i:02d}", trap, f"opp{i}", "Victim", "white") for i in range(4)]
    games += [_fake_game("g10", ["e4", "e5", "Nf3", "Nc6"], "opp9", "Victim", None)]
    store_games(con, [parse_game(g) for g in games])
    cfg = _cfg(tmp_path)
    evaluate_pending(con, cfg)

    traps = find_traps(con, cfg, "victim")
    assert traps, "debería encontrar la trampa"
    t = traps[0]
    assert t["usual_san"] == "Nf6"
    assert t["refutation_san"] == "Qxf7#"
    assert t["punisher_color"] == "white"
    assert t["forcing"] and t["difficulty"] == "fácil"
    assert t["games_after_mistake"] == 4 and t["refutation_found"] == 4
    # La línea llega a la posición, agrega su error y sigue con la refutación.
    assert [m["san"] for m in t["line"]][:7] == trap
    assert t["line"][t["line_mistake_index"]]["san"] == "Nf6"
