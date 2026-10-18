import json

from tegchess.config import Config
from tegchess.pgn_import import import_folder, search_players
from tegchess.report import build_report

PGN = """[Event "Abierto de Rosario"]
[Site "Rosario ARG"]
[Date "2024.03.10"]
[Round "1"]
[White "Pérez, Juan"]
[Black "Gómez, Ana"]
[Result "1-0"]
[WhiteElo "1950"]
[BlackElo "1880"]
[ECO "C50"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. c3 Nf6 5. d4 exd4 6. cxd4 Bb4+ 1-0

[Event "Abierto de Rosario"]
[Date "2024.03.11"]
[Round "2"]
[White "Gomez, Ana"]
[Black "Perez,Juan"]
[Result "1/2-1/2"]
[WhiteElo "1880"]
[BlackElo "1950"]
[TimeControl "5400+30"]

1. d4 d5 2. c4 e6 3. Nc3 Nf6 1/2-1/2

[Event "Blitz del club"]
[Date "2024.??.??"]
[White "Pérez, Juan"]
[Black "López, Carlos"]
[Result "0-1"]

1. e4 c5 2. Nf3 d6 0-1

[Event "Sin terminar"]
[White "Pérez, Juan"]
[Black "X"]
[Result "*"]

1. e4 *
"""


def _cfg(tmp_path, **pgn):
    return Config.model_validate({
        "source": "pgn", "data_dir": str(tmp_path / "data"),
        "pgn": {"me": "Gómez, Ana", "opponents": ["Pérez, Juan"], **pgn},
    })


def test_import_folder_and_report(con, tmp_path):
    cfg = _cfg(tmp_path)
    cfg.pgn_path.mkdir(parents=True)
    (cfg.pgn_path / "rivales.pgn").write_text(PGN, encoding="utf-8")

    summary = import_folder(con, cfg)
    assert summary["files"]["rivales.pgn"] == 3          # la partida sin terminar no entra
    rows = dict(con.execute("SELECT white, count(*) FROM games WHERE origin = 'pgn' GROUP BY 1").fetchall())
    # "Pérez, Juan" y "Perez,Juan" son la misma persona (sin tildes y con espacios normalizados).
    assert rows["perez, juan"] == 2 and rows["gomez, ana"] == 1
    perf = dict(con.execute("SELECT pgn_file || ':' || event_round, perf FROM (SELECT pgn_file, source || "
                            "coalesce(cast(num_plies AS VARCHAR), '') AS event_round, perf FROM games)").fetchall())
    assert "blitz" in perf.values() and "classical" in perf.values()

    # Reimportar sin cambios no duplica; si el archivo desaparece, se borran sus partidas.
    assert import_folder(con, cfg)["files"]["rivales.pgn"] == "sin cambios"
    players = search_players(cfg, "perez")
    assert players and players[0]["games"] == 3 and players[0]["name"].startswith("P")
    assert search_players(cfg, "lopez carlos")[0]["games"] == 1

    rep = build_report(con, cfg.active(), "Pérez, Juan")
    assert rep["meta"]["rival_name"] == "Pérez, Juan" and rep["meta"]["source"] == "pgn"
    assert rep["rival"]["profile"]["summary"]["games"] == 3
    json.dumps(rep, default=str)

    (cfg.pgn_path / "rivales.pgn").unlink()
    summary = import_folder(con, cfg)
    assert summary["removed"] == ["rivales.pgn"]
    assert con.execute("SELECT count(*) FROM games WHERE origin = 'pgn'").fetchone()[0] == 0


def test_lichess_scope_ignores_pgn_games(con, tmp_path):
    cfg = _cfg(tmp_path)
    cfg.pgn_path.mkdir(parents=True)
    (cfg.pgn_path / "a.pgn").write_text(PGN, encoding="utf-8")
    import_folder(con, cfg)
    lichess = Config.model_validate({"data_dir": str(tmp_path / "data"), "opponents": ["Pérez, Juan"],
                                     "download": {"perf_types": [], "max_games": None, "rated_only": False}})
    rep = build_report(con, lichess, "Pérez, Juan")
    assert rep["rival"]["profile"]["summary"]["games"] == 0
