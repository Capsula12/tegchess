"""Cruce de mi repertorio contra el de un rival.

Para cada color mío (el rival juega el contrario) se comparan:
- Familias de apertura que juegamos los dos: cómo salimos de la apertura (motor) y cómo rendimos.
- Posiciones a las que llegamos los dos (por EPD, así cuentan las transposiciones): el
  "campo de batalla". En las que me toca mover se evalúan mis opciones según cómo le fue
  al rival después de cada una.

Puntaje esperado (0-100, desde mí) = expectativa por Elo de este cruce + ajustes
(ver analysis/expectation.py): la ventaja según el motor (la señal estable) y el rendimiento
sobre el Elo de cada uno en esa posición, muy suavizado porque con pocas partidas es ruido.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import duckdb

from tegchess.analysis.expectation import confidence, expected_with_adjustments, wilson
from tegchess.analysis.lines import line_to_position, opening_at, san
from tegchess.analysis.openings import opening_summary, repeated_positions
from tegchess.analysis.scope import scope_cte
from tegchess.config import Config
from tegchess.engine import epd_to_fen
from tegchess.metrics import eval_cp

# Residuo de una partida: resultado − esperado por Elo (0 si falta algún rating).
RESIDUAL_SQL = "coalesce(s.score - 1 / (1 + pow(10, (s.opp_rating - s.rating) / 400.0)), 0)"


def opposite(color: str) -> str:
    return "black" if color == "white" else "white"


def _expected(base: float, eval_for_me: float | None, mine: tuple[float | None, int],
              theirs: tuple[float | None, int]) -> dict[str, Any]:
    out = expected_with_adjustments(base, eval_for_me, mine, theirs)
    out["confidence"] = confidence(min(mine[1], theirs[1]) if theirs[1] else mine[1])
    return out


# --- datos por posición --------------------------------------------------


@dataclass
class PosStats:
    n: int
    score: float
    ply: int
    residual: float = 0.0   # suma de (resultado − esperado por Elo)


def _positions(con: duckdb.DuckDBPyConnection, cfg: Config, player: str, color: str,
               max_ply: int) -> tuple[dict[str, PosStats], dict[str, list[dict[str, Any]]]]:
    """(stats por posición, jugadas propias por posición) del jugador con un color."""
    cte, params = scope_cte(cfg, player, color)
    pos_rows = con.execute(f"""
        WITH {cte}
        SELECT m.epd_before, count(DISTINCT s.game_id), avg(s.score), min(m.ply), sum({RESIDUAL_SQL})
        FROM scope s JOIN moves m ON m.game_id = s.game_id
        WHERE m.ply <= ?
        GROUP BY 1
    """, [*params, max_ply]).fetchall()
    stats = {epd: PosStats(n, score, ply, res or 0.0) for epd, n, score, ply, res in pos_rows}

    move_rows = con.execute(f"""
        WITH {cte}
        SELECT q.epd_before, q.uci, any_value(q.san), any_value(q.epd_after), count(*), avg(s.score),
               avg(q.cpl), avg(q.wp_loss), sum({RESIDUAL_SQL})
        FROM scope s JOIN move_quality q ON q.game_id = s.game_id AND q.color = s.color
        WHERE q.ply <= ?
        GROUP BY 1, 2
    """, [*params, max_ply]).fetchall()
    moves: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for epd, uci, s, after, n, score, cpl, wpl, res in move_rows:
        moves[epd].append({"uci": uci, "san": s, "epd_after": after, "n": n, "score": score,
                           "cpl": cpl, "wp_loss": wpl, "residual": res or 0.0})
    for lst in moves.values():
        lst.sort(key=lambda m: m["n"], reverse=True)
    return stats, moves


def _evals(con: duckdb.DuckDBPyConnection, epds: list[str]) -> dict[str, tuple[int | None, str | None]]:
    """EPD -> (evaluación en cp desde las blancas, mejor jugada)."""
    if not epds:
        return {}
    out = {}
    for i in range(0, len(epds), 1000):
        chunk = epds[i:i + 1000]
        rows = con.execute(
            f"SELECT epd, cp, mate, best_uci FROM evals WHERE epd IN ({','.join('?' for _ in chunk)})", chunk
        ).fetchall()
        for epd, cp, mate, best in rows:
            out[epd] = (eval_cp(cp, mate), best)
    return out


def _pct(x: float | None) -> float | None:
    return None if x is None else round(100 * x, 1)


# --- cruce ----------------------------------------------------------------------


def family_matchup(con: duckdb.DuckDBPyConnection, cfg: Config, me: str, rival: str,
                   my_color: str, base: float = 0.5) -> list[dict[str, Any]]:
    """Familias que jugamos los dos (yo con my_color, él con el contrario).

    La evaluación al salir de la apertura es la señal estable: se promedia la mía (lo que suelo
    conseguir) con la suya vista desde mí (lo que él suele conceder).
    """
    mine = {o["opening"]: o for o in opening_summary(con, cfg, me, my_color)}
    theirs = {o["opening"]: o for o in opening_summary(con, cfg, rival, opposite(my_color))}
    out = []
    for name in mine.keys() & theirs.keys():
        a, b = mine[name], theirs[name]
        evals = [x for x in (a["avg_eval_out"], None if b["avg_eval_out"] is None else -b["avg_eval_out"])
                 if x is not None]
        eval_me = sum(evals) / len(evals) if evals else None
        exp = _expected(base, eval_me, (a["residual_sum"], a["games"]), (b["residual_sum"], b["games"]))
        out.append({
            "opening": name,
            "eco": a["eco"] or b["eco"],
            "my_games": a["games"], "my_score_pct": a["score_pct"], "my_cpl": a["avg_cpl"],
            "my_over_pct": a["over_pct"], "my_eval_out": a["avg_eval_out"], "my_ci": a["score_ci"],
            "rival_games": b["games"], "rival_score_pct": b["score_pct"], "rival_cpl": b["avg_cpl"],
            "rival_over_pct": b["over_pct"], "rival_eval_out": b["avg_eval_out"], "rival_ci": b["score_ci"],
            "eval_for_me": None if eval_me is None else round(eval_me),
            **exp,
            # Juego peor que él esta apertura (más pérdida de centipeones): a reforzar.
            "reinforce": a["avg_cpl"] is not None and b["avg_cpl"] is not None and a["avg_cpl"] > b["avg_cpl"] + 5,
        })
    out.sort(key=lambda r: (r["expected_pct"], min(r["my_games"], r["rival_games"])), reverse=True)
    return out


def battle_positions(con: duckdb.DuckDBPyConnection, cfg: Config, me: str, rival: str, my_color: str,
                     max_ply: int | None = None, min_games: int = 2, base: float = 0.5) -> list[dict[str, Any]]:
    """Posiciones a las que llegamos los dos, con puntaje esperado y, si me toca mover, mis opciones."""
    max_ply = max_ply or max(cfg.analysis.opening_depth_plies, 16)
    riv_color = opposite(my_color)
    my_stats, my_moves = _positions(con, cfg, me, my_color, max_ply)
    rv_stats, rv_moves = _positions(con, cfg, rival, riv_color, max_ply)

    shared = [epd for epd in my_stats.keys() & rv_stats.keys()
              if my_stats[epd].n >= min_games and rv_stats[epd].n >= min_games]
    after_epds = [m["epd_after"] for epd in shared for m in my_moves.get(epd, []) + rv_moves.get(epd, [])]
    evals = _evals(con, shared + after_epds)
    sign = 1 if my_color == "white" else -1

    def eval_me(epd: str) -> float | None:
        e = evals.get(epd, (None, None))[0]
        return None if e is None else sign * e

    rival_total = max((st.n for st in rv_stats.values()), default=0)   # partidas suyas con ese color
    out = []
    for epd in shared:
        a, b = my_stats[epd], rv_stats[epd]
        to_move = "me" if (" w " in epd) == (my_color == "white") else "rival"
        e = eval_me(epd)
        item: dict[str, Any] = {
            "epd": epd,
            "fen": epd_to_fen(epd),
            "ply": min(a.ply, b.ply),
            "to_move": to_move,
            "my_games": a.n, "my_score_pct": _pct(a.score), "my_ci": wilson(a.score, a.n),
            "rival_games": b.n, "rival_score_pct": _pct(b.score), "rival_ci": wilson(b.score, b.n),
            # Qué tan seguido llega él a esta posición con ese color.
            "rival_reach_pct": round(100 * b.n / rival_total, 1) if rival_total else None,
            "eval_for_me": e,
            "best_san": san(epd, evals.get(epd, (None, None))[1]),
            **_expected(base, e, (a.residual, a.n), (b.residual, b.n)),
        }
        if to_move == "me":
            # Mis opciones: cómo me fue a mí y cómo le fue a él después de cada jugada.
            opts = []
            for m in my_moves.get(epd, [])[:5]:
                after = rv_stats.get(m["epd_after"])
                opts.append({
                    "san": m["san"], "uci": m["uci"], "my_games": m["n"], "my_score_pct": _pct(m["score"]),
                    "rival_games": after.n if after else 0, "rival_score_pct": _pct(after.score) if after else None,
                    "eval_for_me": eval_me(m["epd_after"]),
                    **_expected(base, eval_me(m["epd_after"]), (m["residual"], m["n"]),
                                (after.residual if after else None, after.n if after else 0)),
                })
            opts.sort(key=lambda o: o["expected_pct"], reverse=True)
            # Para recomendar una jugada hace falta algo de muestra (mía o de él después de ella).
            item["options"] = [o for o in opts if o["my_games"] + o["rival_games"] >= 3] or opts[:1]
            item["usual_san"] = my_moves[epd][0]["san"] if my_moves.get(epd) else None
        else:
            # Lo que suele jugar él y cómo me fue a mí contra cada respuesta.
            opts = []
            for m in rv_moves.get(epd, [])[:5]:
                after = my_stats.get(m["epd_after"])
                opts.append({
                    "san": m["san"], "uci": m["uci"], "rival_games": m["n"], "rival_score_pct": _pct(m["score"]),
                    "my_games": after.n if after else 0, "my_score_pct": _pct(after.score) if after else None,
                    "rival_wp_loss": None if m["wp_loss"] is None else round(m["wp_loss"], 1),
                })
            item["options"] = opts
            item["usual_san"] = rv_moves[epd][0]["san"] if rv_moves.get(epd) else None
        out.append(item)
    return out


def select_battles(battles: list[dict[str, Any]], limit: int = 8) -> dict[str, list[dict[str, Any]]]:
    """Las más favorables y las más peligrosas, sin repetir y priorizando posiciones con muestra."""
    pool = [b for b in battles if b["ply"] >= 2]
    favorable = sorted(pool, key=lambda b: (b["expected_pct"], b["ply"]), reverse=True)[:limit]
    danger = [b for b in sorted(pool, key=lambda b: (b["expected_pct"], -b["ply"])) if b not in favorable][:limit]
    return {"favorable": favorable, "danger": danger}


def reinforce_positions(con: duckdb.DuckDBPyConnection, cfg: Config, me: str, rival: str, my_color: str,
                        limit: int = 10) -> list[dict[str, Any]]:
    """Mis posiciones repetidas con pérdida alta que también aparecen en las partidas del rival."""
    mine = [r for r in repeated_positions(con, cfg, me, color=my_color, limit=60) if (r["avg_wp_loss"] or 0) >= 4]
    if not mine:
        return []
    cte, params = scope_cte(cfg, rival, opposite(my_color))
    epds = [r["epd"] for r in mine]
    rows = con.execute(f"""
        WITH {cte}
        SELECT m.epd_before, count(DISTINCT s.game_id), avg(s.score)
        FROM scope s JOIN moves m ON m.game_id = s.game_id
        WHERE m.epd_before IN ({','.join('?' for _ in epds)})
        GROUP BY 1
    """, [*params, *epds]).fetchall()
    rival_at = {epd: (n, score) for epd, n, score in rows}
    out = []
    for r in mine:
        n, score = rival_at.get(r["epd"], (0, None))
        if not n:
            continue
        out.append({**r, "fen": epd_to_fen(r["epd"]), "rival_games": n, "rival_score_pct": _pct(score),
                    "best_san": san(r["epd"], r["best_uci"])})
    out.sort(key=lambda r: r["avg_wp_loss"] * r["occurrences"], reverse=True)
    return out[:limit]


def attach_lines(con: duckdb.DuckDBPyConnection, items: list[dict[str, Any]]) -> None:
    for it in items:
        it["line"] = line_to_position(con, it["epd"])
        it.setdefault("opening", opening_at(con, it["epd"]))
