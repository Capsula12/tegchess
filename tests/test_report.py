import json

from fastapi.testclient import TestClient

from tegchess.config import Config, save_config
from tegchess.ingest import parse_game, store_games
from tegchess.report import build_report, export_static, write_report


def _cfg(tmp_path):
    return Config.model_validate({
        "me": "german11",
        "opponents": ["lucky09"],
        "download": {"perf_types": ["blitz"], "max_games": None, "since": None, "rated_only": False},
        "analysis": {"min_occurrences": 2},
        "data_dir": str(tmp_path / "data"),
    })


def test_build_report_structure(con, tmp_path, raw_games):
    store_games(con, [parse_game(r) for r in raw_games])
    cfg = _cfg(tmp_path)
    from tegchess.report import build_me_report

    me = build_me_report(con, cfg)
    assert me["meta"]["key"] == "_me" and me["comparison"]["metrics"] and me["improvement"]["areas"]
    assert write_report(me, cfg.reports_path).name == "_me.json"
    rep = build_report(con, cfg, "Lucky09")
    assert rep["meta"]["rival"] == "lucky09"
    assert rep["rival"]["profile"]["summary"]["games"] == 1
    assert rep["me"]["profile"]["summary"]["games"] == len(raw_games)
    assert set(rep["rival"]["trees"]) == {"white", "black"}
    # Cada posición que puede ir al tablero trae su evaluación (aunque sea None) para la barra.
    node = rep["rival"]["trees"]["white"]
    assert "cp" in node and "mate" in node and "start_cp" in rep["meta"]
    assert all("cp" in c for c in node["children"])
    # Serializable y exportable a HTML estático.
    path = write_report(rep, cfg.reports_path)
    assert json.loads(path.read_text(encoding="utf-8"))["meta"]["rival"] == "lucky09"
    index = export_static([rep], tmp_path / "export")
    assert index.exists()
    assert "TEGCHESS_STATIC" in (index.parent / "static-data.js").read_text(encoding="utf-8")


def test_server_endpoints(con, tmp_path, raw_games, monkeypatch):
    store_games(con, [parse_game(r) for r in raw_games])
    cfg = _cfg(tmp_path)
    write_report(build_report(con, cfg, "lucky09"), cfg.reports_path)
    cfg_file = tmp_path / "config.yaml"
    save_config(cfg, cfg_file)
    monkeypatch.setenv("TEGCHESS_CONFIG", str(cfg_file))

    from tegchess.server import create_app

    client = TestClient(create_app())
    assert client.get("/").status_code == 200
    assert client.get("/api/reports").json()[0]["rival"] == "lucky09"
    assert client.get("/api/reports/lucky09").json()["meta"]["rival"] == "lucky09"
    assert client.get("/api/reports/..%2Fconfig").status_code == 404
    assert client.get("/api/reports").headers["cache-control"] == "no-store"
    assert client.get("/api/me").status_code == 404   # todavía no se generó el perfil propio
    assert client.get("/api/config").json()["me"] == "german11"
    new = client.get("/api/config").json()
    new["opponents"] = ["a", "b"]
    assert client.put("/api/config", json=new).status_code == 200
    assert client.get("/api/config").json()["opponents"] == ["a", "b"]
