from tegchess.analysis.prep import prep_section
from tegchess.config import Config
from tegchess.ingest import parse_game, store_games


def _game(i: int, sans: list[str], white: str, black: str) -> dict:
    return {
        "id": f"p{i:03d}", "rated": True, "variant": "standard", "speed": "blitz", "perf": "blitz",
        "createdAt": 1790000000000 + i * 1000, "status": "resign", "winner": "white",
        "players": {"white": {"user": {"name": white, "id": white.lower()}, "rating": 1500},
                    "black": {"user": {"name": black, "id": black.lower()}, "rating": 1500}},
        "moves": " ".join(sans),
    }


def test_prep_main_line_follows_what_he_repeats(con, tmp_path):
    # El rival (negras) contesta siempre 1.e4 con ...c5 y después ...d6.
    games = [_game(i, ["e4", "c5", "Nf3", "d6", "d4"], f"opp{i}", "Rival") for i in range(8)]
    games += [_game(20, ["d4", "d5"], "opp20", "Rival")]
    store_games(con, [parse_game(g) for g in games])
    cfg = Config.model_validate({"opponents": ["Rival"], "data_dir": str(tmp_path / "data"),
                                 "download": {"perf_types": ["blitz"], "max_games": None, "rated_only": False}})
    pr = prep_section(con, cfg, "rival", "white")
    assert pr["games"] == 9
    sans = [m["san"] for m in pr["main_line"]]
    assert sans[:4] == ["e4", "c5", "Nf3", "d6"]
    # 8 de 8 suavizado con 2 ficticias: 80% en cada jugada suya.
    assert pr["main_line"][1]["share_pct"] == 80
    assert 25 <= pr["main_line_reach_pct"] <= 80
    assert pr["targets"] == []   # sin evaluaciones no hay objetivos
