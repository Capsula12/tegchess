import base64
import json
from pathlib import Path

import chess
import pytest
from fastapi.testclient import TestClient

from tegchess.config import Config, find_stockfish, save_config
from tegchess.position import analyse, validate
from tegchess.vision import Profile, calibrate, find_board, list_profiles, load_image, recognize

FIXTURES = Path(__file__).parent / "fixtures"
MID = "2r3k1/5pp1/p3p2p/1p1nP3/3P4/P4N2/1P3PPP/2R3K1"


def test_find_board_in_screenshot():
    box = find_board(load_image(FIXTURES / "board_start.png"))
    assert 330 < box.size * 8 < 370          # tablero de 360 px
    assert abs(box.x0 - 20) < 4 and abs(box.y0 - 20) < 4


def test_builtin_lichess_profile_recognizes_flipped_middlegame():
    r = recognize(load_image(FIXTURES / "board_mid_flipped.png"), list_profiles())
    assert r["placement"] == MID
    assert r["white_bottom"] is False
    assert r["confidence"] > 0.8


def test_calibrate_from_starting_position_roundtrip():
    rgb = load_image(FIXTURES / "board_start.png")
    profile, _box, white_bottom = calibrate(rgb, "prueba")
    assert white_bottom
    again = Profile.from_json(json.loads(json.dumps(profile.to_json())))
    r = recognize(rgb, [again])
    assert r["placement"] == chess.STARTING_BOARD_FEN and r["castling"] == "KQkq"
    # Con el perfil calibrado también se lee otra posición del mismo juego de piezas.
    assert recognize(load_image(FIXTURES / "board_mid_flipped.png"), [again])["placement"] == MID


def test_calibrate_rejects_non_start_position():
    with pytest.raises(ValueError):
        calibrate(load_image(FIXTURES / "board_mid_flipped.png"), "x")


def test_validate_explains_problems():
    with pytest.raises(ValueError, match="rey negro"):
        validate("8/8/8/8/8/8/8/K7 w - - 0 1")
    assert validate(chess.STARTING_FEN).is_valid()


@pytest.mark.skipif(find_stockfish() is None, reason="Stockfish no instalado")
def test_analyse_finds_mate_in_one(tmp_path):
    cfg = Config.model_validate({"data_dir": str(tmp_path), "engine": {"workers": 1}})
    a = analyse(cfg, "6k1/5ppp/8/8/8/8/5PPP/3R2K1 w - - 0 1", multipv=2, depth=10)
    assert a["lines"][0]["san"] == "Rd8#" and a["lines"][0]["mate"] == 1
    assert a["lines"][0]["steps"][0]["fen"].startswith("3R2k1")


def test_vision_endpoints(tmp_path, monkeypatch):
    cfg = Config.model_validate({"data_dir": str(tmp_path / "data")})
    cfg_file = tmp_path / "config.yaml"
    save_config(cfg, cfg_file)
    monkeypatch.setenv("TEGCHESS_CONFIG", str(cfg_file))
    from tegchess.server import create_app

    client = TestClient(create_app())
    img = "data:image/png;base64," + base64.b64encode((FIXTURES / "board_mid_flipped.png").read_bytes()).decode()
    r = client.post("/api/vision/recognize", json={"image": img})
    assert r.status_code == 200 and r.json()["placement"] == MID
    start = "data:image/png;base64," + base64.b64encode((FIXTURES / "board_start.png").read_bytes()).decode()
    assert client.post("/api/vision/calibrate", json={"image": start, "name": "Mi sitio"}).status_code == 200
    assert "Mi sitio" in client.get("/api/vision/profiles").json()
    assert client.post("/api/position/analyze", json={"fen": "8/8/8/8/8/8/8/K7 w - - 0 1"}).status_code == 422
