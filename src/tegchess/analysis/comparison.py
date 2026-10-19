"""Mi perfil: mis métricas al lado de las de mis rivales, y mis mejores y peores aperturas.

Las métricas son las que existen para todos con el mismo criterio: los rivales solo se analizan hasta
`engine.max_ply`, así que la calidad de juego se mide en la apertura (primeros 20 plies) para todos.
"""

from __future__ import annotations

from statistics import mean
from typing import Any

import duckdb

from tegchess.analysis.clock import TROUBLE_FRACTION
from tegchess.analysis.expectation import ENGINE_WEIGHT, confidence, shrunk_residual
from tegchess.analysis.openings import OPENING_PLIES, opening_summary
from tegchess.analysis.scope import scope_cte
from tegchess.config import Config, normalize_player
from tegchess.db import fetch_dicts
from tegchess.metrics import win_pct

MIN_GAMES = 20   # rivales con menos partidas no entran en el promedio, el mejor ni el puesto

# (clave, etiqueta, formato, más es mejor)
METRICS: list[tuple[str, str, str, bool]] = [
    ("rating", "Rating actual", "int", True),
    ("over_pct", "Rendimiento sobre el Elo", "signed", True),
    ("over_white_pct", "… con blancas", "signed", True),
    ("over_black_pct", "… con negras", "signed", True),
    ("opening_cpl", "Pérdida media en la apertura (CPL, 20 plies)", "float", False),
    ("opening_error_pct", "Errores en la apertura (% de jugadas)", "pct", False),
    ("eval_out", "Evaluación al salir de la apertura (ply 20)", "eval", True),
    ("lost_on_time_pct", "Derrotas por tiempo (% de las derrotas)", "pct", False),
    ("trouble_pct", f"Partidas con apuro (< {int(100 * TROUBLE_FRACTION)}% del reloj)", "pct", False),
    ("clock_move20_pct", "Reloj que queda en la jugada 20", "pct", True),
    ("accuracy", "Precisión de Lichess (partidas analizadas)", "pct", True),
    ("avg_moves", "Jugadas por partida", "float", None),
    ("resign_pct", "Partidas que terminan por abandono", "pct", None),
]


def player_metrics(con: duckdb.DuckDBPyConnection, cfg: Config, player: str) -> dict[str, Any]:
    cte, params = scope_cte(cfg, player)
    base = fetch_dicts(con, f"""
        WITH {cte},
        g AS (SELECT *, score - 1 / (1 + pow(10, (opp_rating - rating) / 400.0)) AS residual FROM scope)
        SELECT count(*) AS games,
               arg_max(rating, created_at) AS rating,
               round(100 * avg(residual), 1) AS over_pct,
               round(100 * avg(residual) FILTER (WHERE color = 'white'), 1) AS over_white_pct,
               round(100 * avg(residual) FILTER (WHERE color = 'black'), 1) AS over_black_pct,
               round(100.0 * count(*) FILTER (WHERE status = 'outoftime' AND score = 0)
                     / nullif(count(*) FILTER (WHERE score = 0), 0), 1) AS lost_on_time_pct,
               round(avg(accuracy), 1) AS accuracy,
               round(avg(num_plies) / 2, 1) AS avg_moves,
               round(100.0 * count(*) FILTER (WHERE status = 'resign') / count(*), 1) AS resign_pct
        FROM g
    """, params)[0]
    quality = fetch_dicts(con, f"""
        WITH {cte}
        SELECT round(avg(least(q.cpl, 1000)), 1) AS opening_cpl,
               round(100.0 * count(*) FILTER (WHERE q.judgment IN ('mistake', 'blunder')) / count(*), 1)
                   AS opening_error_pct
        FROM move_quality q JOIN scope s ON s.game_id = q.game_id AND q.color = s.color
        WHERE q.ply <= {OPENING_PLIES} AND q.judgment IS NOT NULL
    """, params)[0]
    eval_out = con.execute(f"""
        WITH {cte}
        SELECT round(avg(CASE s.color WHEN 'white' THEN 1 ELSE -1 END * eval_cp(e.cp, e.mate)))
        FROM scope s JOIN moves m ON m.game_id = s.game_id AND m.ply = {OPENING_PLIES}
        JOIN evals e ON e.epd = m.epd_after
    """, params).fetchone()[0]
    clock = fetch_dicts(con, f"""
        WITH {cte},
        mv AS (
            SELECT s.game_id, (m.ply + 1) // 2 AS move_no, m.clock_cs / (s.clock_initial * 100.0) AS frac
            FROM scope s JOIN moves m ON m.game_id = s.game_id AND m.color = s.color
            WHERE s.clock_initial > 0 AND m.clock_cs IS NOT NULL
        )
        SELECT round(100.0 * count(DISTINCT game_id) FILTER (WHERE frac < {TROUBLE_FRACTION})
                     / nullif(count(DISTINCT game_id), 0), 1) AS trouble_pct,
               round(100 * avg(frac) FILTER (WHERE move_no = 20), 1) AS clock_move20_pct
        FROM mv
    """, params)[0]
    return {"player": player, **base, **quality, "eval_out": eval_out, **clock}


def compare(me: dict[str, Any], rivals: list[dict[str, Any]], names: dict[str, str]) -> list[dict[str, Any]]:
    """Una fila por métrica: yo, promedio de los rivales, el mejor de ellos y mi puesto en el grupo."""
    rows = []
    for key, label, fmt, higher in METRICS:
        mine = me.get(key)
        vals = [(r["player"], r.get(key)) for r in rivals
                if r.get(key) is not None and (r.get("games") or 0) >= MIN_GAMES]
        if mine is None and not vals:
            continue
        avg = round(mean(v for _, v in vals), 1) if vals else None
        best = None
        rank = None
        if higher is not None and vals:
            best_p, best_v = (max if higher else min)(vals, key=lambda pv: pv[1])
            best = {"name": names.get(best_p, best_p), "value": best_v}
            if mine is not None:
                better = sum(1 for _, v in vals if (v > mine if higher else v < mine))
                rank = better + 1
        verdict = None
        if higher is not None and mine is not None and avg is not None:
            diff = (mine - avg) if higher else (avg - mine)
            scale = max(abs(avg) * 0.1, 1.0)
            verdict = "mejor" if diff > scale else "peor" if diff < -scale else "parecido"
        rows.append({"key": key, "label": label, "format": fmt, "me": mine, "rivals_avg": avg, "best": best,
                     "rank": rank, "of": len(vals) + 1, "verdict": verdict})
    return rows


def opening_ranking(con: duckdb.DuckDBPyConnection, cfg: Config, me: str, min_games: int = 8,
                    limit: int = 6) -> dict[str, list[dict[str, Any]]]:
    """Mis aperturas (familia y color) ordenadas por rendimiento: motor al salir + resultado sobre el Elo suavizado."""
    rows = []
    for color in ("white", "black"):
        for o in opening_summary(con, cfg, me, color):
            if o["games"] < min_games:
                continue
            engine = 0.0 if o["avg_eval_out"] is None else ENGINE_WEIGHT * (win_pct(o["avg_eval_out"]) - 50)
            results = 100 * shrunk_residual(o["residual_sum"], o["games"])
            rows.append({**o, "color": color, "engine_pts": round(engine, 1), "results_pts": round(results, 1),
                         "rating_pts": round(engine + results, 1), "confidence": confidence(o["games"])})
    rows.sort(key=lambda r: r["rating_pts"], reverse=True)
    best = [r for r in rows if r["rating_pts"] > 0][:limit]
    worst = [r for r in reversed(rows) if r["rating_pts"] < 0][:limit]
    return {"best": best, "worst": worst, "all": rows}


def profile_comparison(con: duckdb.DuckDBPyConnection, cfg: Config) -> dict[str, Any]:
    me = normalize_player(cfg.me)
    rivals = [normalize_player(o) for o in cfg.opponents if normalize_player(o) != me]
    mine = player_metrics(con, cfg, me)
    theirs = [player_metrics(con, cfg, r) for r in rivals]
    names = {r: cfg.display_name(r) for r in rivals}
    return {
        "metrics": compare(mine, theirs, names),
        "players": [{"name": names[t["player"]], **t} for t in theirs],
        "me": mine,
        "openings": opening_ranking(con, cfg, me),
    }
