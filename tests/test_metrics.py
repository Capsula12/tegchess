import pytest

from tegchess import db
from tegchess.metrics import eval_cp, judgment, move_loss, win_pct


def test_eval_cp_caps_and_mate():
    assert eval_cp(2500, None) == 1000
    assert eval_cp(-2500, None) == -1000
    assert eval_cp(None, 3) == 1000
    assert eval_cp(None, -1) == -1000
    assert eval_cp(None, None) is None


def test_win_pct_symmetry():
    assert win_pct(0) == pytest.approx(50)
    assert win_pct(300) + win_pct(-300) == pytest.approx(100)
    assert win_pct(1000) > 97


def test_move_loss_from_mover_perspective():
    # Blancas: de +50 a -150 => pierden 200
    cpl, wpl = move_loss(50, -150, mover_is_white=True)
    assert cpl == 200 and wpl > 0
    # Negras: de +50 a -150 (desde blancas) => las negras mejoraron, pérdida 0
    cpl, wpl = move_loss(50, -150, mover_is_white=False)
    assert cpl == 0 and wpl == 0


def test_judgment_thresholds():
    assert judgment(4.9) == "ok"
    assert judgment(5) == "inaccuracy"
    assert judgment(10) == "mistake"
    assert judgment(20) == "blunder"


def test_sql_matches_python(con):
    """La vista move_quality usa las mismas fórmulas que metrics.py."""
    con.execute("INSERT INTO moves (game_id, ply, color, san, uci, epd_before, epd_after) "
                "VALUES ('g', 1, 'black', 'x', 'x', 'A', 'B')")
    db.bulk_insert(con, "evals", [
        {"epd": "A", "cp": -30, "mate": None, "source": "test"},
        {"epd": "B", "cp": 250, "mate": None, "source": "test"},
    ])
    row = con.execute("SELECT cpl, wp_loss, judgment FROM move_quality").fetchone()
    cpl, wpl = move_loss(-30, 250, mover_is_white=False)
    assert row[0] == cpl == 280
    assert row[1] == pytest.approx(wpl)
    assert row[2] == judgment(wpl)
