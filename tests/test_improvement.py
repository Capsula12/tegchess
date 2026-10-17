from tegchess.analysis.improvement import _endgame_type, _error_kind, improvement_section
from tegchess.config import Config
from tegchess.ingest import parse_game, store_games


def _cfg(tmp_path):
    return Config.model_validate({
        "me": "german11",
        "download": {"perf_types": ["blitz"], "max_games": None, "since": None, "rated_only": False},
        "data_dir": str(tmp_path / "data"),
    })


def test_improvement_section_runs_and_sorts(con, tmp_path, raw_games):
    store_games(con, [parse_game(r) for r in raw_games])
    imp = improvement_section(con, _cfg(tmp_path), "german11")
    keys = {a["key"] for a in imp["areas"]}
    assert keys == {"time", "openings", "conversion", "errors", "tendencies", "endgames"}
    costs = [a["cost_per_100"] for a in imp["areas"]]
    assert costs == sorted(costs, reverse=True)
    assert set(imp["priorities"]) <= keys
    assert 0 <= imp["eval_coverage_pct"] <= 100


def test_error_kind():
    start = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -"
    # Posición con una captura gratis disponible para las blancas (dama en d5 sin defensa).
    epd = "rnb1kbnr/pppp1ppp/8/3qp3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq -"
    assert _error_kind(epd, "g1f3", "e4d5", None) == "missed_tactic"
    assert _error_kind(start, "a2a3", "e2e4", "e7e5") == "positional"
    # Tras 1.e4 e5, 2.f3 permite ...Qh4+ (jaque como mejor respuesta del rival).
    assert _error_kind("rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq -", "f2f3", "g1f3", "d8h4") == "allowed_tactic"


def test_endgame_type():
    assert _endgame_type("8/5k2/8/8/8/8/5K2/8 w - -") == "De peones"
    assert _endgame_type("8/5k2/8/3r4/8/8/5K2/R7 w - -") == "De torres"
    assert _endgame_type("8/5k2/8/3b4/8/8/5K2/R7 w - -") == "Torres y piezas menores"
