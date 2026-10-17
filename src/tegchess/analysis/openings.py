"""Resumen de aperturas, árbol de repertorio y posiciones repetidas."""

from __future__ import annotations

from typing import Any

import chess
import duckdb

from tegchess.analysis.expectation import confidence, wilson
from tegchess.analysis.scope import scope_cte
from tegchess.config import Config
from tegchess.db import fetch_dicts

# Plies que se consideran "apertura" para medir la pérdida media de centipeones.
OPENING_PLIES = 20


def opening_summary(con: duckdb.DuckDBPyConnection, cfg: Config, player: str, color: str,
                    by: str = "family") -> list[dict[str, Any]]:
    """Resultados por apertura (familia o nombre completo) para un jugador y color.

    by: 'family' (p.ej. "Sicilian Defense") o 'name' (variante completa).
    """
    group_col = "opening_family" if by == "family" else "opening_name"
    cte, params = scope_cte(cfg, player, color)
    sql = f"""
    WITH {cte},
    opening_moves AS (
        SELECT q.game_id, avg(q.cpl) AS cpl, avg(q.wp_loss) AS wp_loss,
               count(*) FILTER (WHERE q.judgment IN ('mistake', 'blunder')) AS errors
        FROM move_quality q JOIN scope s ON s.game_id = q.game_id AND q.color = s.color
        WHERE q.ply <= {OPENING_PLIES} AND q.cpl IS NOT NULL
        GROUP BY q.game_id
    ),
    eval_out AS (  -- evaluación al salir de la apertura, desde el jugador
        SELECT s.game_id,
               CASE s.color WHEN 'white' THEN 1 ELSE -1 END * eval_cp(e.cp, e.mate) AS eval
        FROM scope s
        JOIN moves m ON m.game_id = s.game_id AND m.ply = {OPENING_PLIES}
        JOIN evals e ON e.epd = m.epd_after
    )
    SELECT coalesce(s.{group_col}, '(desconocida)') AS opening,
           any_value(s.eco) AS eco,
           count(*) AS games,
           count(*) FILTER (WHERE s.score = 1) AS wins,
           count(*) FILTER (WHERE s.score = 0.5) AS draws,
           count(*) FILTER (WHERE s.score = 0) AS losses,
           round(100 * avg(s.score), 1) AS score_pct,
           avg(s.score) AS _score,
           sum(coalesce(s.score - 1 / (1 + pow(10, (s.opp_rating - s.rating) / 400.0)), 0)) AS residual_sum,
           round(100 * avg(s.score - 1 / (1 + pow(10, (s.opp_rating - s.rating) / 400.0))), 1) AS over_pct,
           round(avg(s.opp_rating)) AS avg_opp_rating,
           round(avg(o.cpl), 1) AS avg_cpl,
           count(o.cpl) AS games_with_evals,
           round(avg(ev.eval)) AS avg_eval_out,
           max(s.created_at) AS last_played
    FROM scope s
    LEFT JOIN opening_moves o ON o.game_id = s.game_id
    LEFT JOIN eval_out ev ON ev.game_id = s.game_id
    GROUP BY 1
    ORDER BY games DESC, opening
    """
    rows = fetch_dicts(con, sql, params)
    for r in rows:
        r["score_ci"] = wilson(r.pop("_score"), r["games"])
        r["confidence"] = confidence(r["games"])
    return rows


def repertoire_tree(con: duckdb.DuckDBPyConnection, cfg: Config, player: str, color: str,
                    max_ply: int | None = None, min_count: int = 2) -> dict[str, Any]:
    """Árbol de jugadas del jugador con un color: frecuencia, puntaje y pérdida por nodo.

    Cada nodo: {san, uci, fen, count, score_pct, avg_cpl, mover, children}. La raíz es la
    posición inicial. Las ramas con menos de `min_count` partidas se podan (salvo las primeras 2 jugadas).
    """
    max_ply = max_ply or cfg.analysis.opening_depth_plies
    cte, params = scope_cte(cfg, player, color)
    sql = f"""
    WITH {cte}
    SELECT q.game_id, q.ply, q.san, q.uci, q.color AS mover, q.cpl, s.score
    FROM move_quality q JOIN scope s ON s.game_id = q.game_id
    WHERE q.ply <= ?
    ORDER BY q.game_id, q.ply
    """
    rows = con.execute(sql, [*params, max_ply]).fetchall()

    root: dict[str, Any] = _node(None, None, chess.STARTING_FEN, None)
    games: dict[str, list[tuple]] = {}
    for r in rows:
        games.setdefault(r[0], []).append(r)

    for moves in games.values():
        node = root
        node["_scores"].append(moves[0][6])
        board = chess.Board()
        for _gid, _ply, san, uci, mover, cpl, score in moves:
            board.push_uci(uci)
            child = node["_children"].get(uci)
            if child is None:
                child = _node(san, uci, board.fen(), mover)
                node["_children"][uci] = child
            child["_scores"].append(score)
            if cpl is not None:
                child["_cpls"].append(cpl)
            node = child

    return _finalize(root, min_count, depth=0)


def _node(san: str | None, uci: str | None, fen: str, mover: str | None) -> dict[str, Any]:
    return {"san": san, "uci": uci, "fen": fen, "mover": mover,
            "_scores": [], "_cpls": [], "_children": {}}


def _finalize(node: dict[str, Any], min_count: int, depth: int) -> dict[str, Any]:
    scores, cpls = node.pop("_scores"), node.pop("_cpls")
    children = node.pop("_children")
    node["count"] = len(scores)
    node["score_pct"] = round(100 * sum(scores) / len(scores), 1) if scores else None
    node["avg_cpl"] = round(sum(cpls) / len(cpls), 1) if cpls else None
    kept = [c for c in children.values() if len(c["_scores"]) >= min_count or depth < 2]
    kept.sort(key=lambda c: len(c["_scores"]), reverse=True)
    node["children"] = [_finalize(c, min_count, depth + 1) for c in kept]
    return node


def repeated_positions(con: duckdb.DuckDBPyConnection, cfg: Config, player: str,
                       color: str | None = None, min_occurrences: int | None = None,
                       max_ply: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Posiciones donde le toca mover al jugador y que repite ≥N veces, con su jugada habitual.

    Ordena por "daño total": pérdida media de % de victoria × veces que aparece.
    """
    min_occ = min_occurrences or cfg.analysis.min_occurrences
    max_ply = max_ply or cfg.engine.max_ply or 60
    cte, params = scope_cte(cfg, player, color)
    sql = f"""
    WITH {cte},
    mine AS (
        SELECT q.*, s.score, s.opening_name
        FROM move_quality q JOIN scope s ON s.game_id = q.game_id AND q.color = s.color
        WHERE q.ply <= ?
    ),
    by_move AS (
        SELECT epd_before, uci, any_value(san) AS san, count(*) AS n,
               avg(cpl) AS cpl, avg(wp_loss) AS wp_loss, avg(score) AS score,
               row_number() OVER (PARTITION BY epd_before ORDER BY count(*) DESC, uci) AS rank
        FROM mine GROUP BY epd_before, uci
    ),
    by_pos AS (
        SELECT epd_before, count(*) AS n, avg(cpl) AS cpl, avg(wp_loss) AS wp_loss,
               avg(score) AS score, min(ply) AS ply, any_value(color) AS color,
               mode(opening_name) AS opening, any_value(best_uci) AS best_uci,
               any_value(before_cp) AS eval_cp, list(DISTINCT game_id)[1:5] AS sample_games
        FROM mine GROUP BY epd_before
        HAVING count(*) >= ?
    )
    SELECT p.epd_before AS epd, p.n AS occurrences, p.ply, p.color, p.opening,
           round(p.cpl, 1) AS avg_cpl, round(p.wp_loss, 1) AS avg_wp_loss,
           round(100 * p.score, 1) AS score_pct, p.eval_cp, p.best_uci,
           m.san AS usual_san, m.uci AS usual_uci, m.n AS usual_count,
           round(m.cpl, 1) AS usual_cpl, round(m.wp_loss, 1) AS usual_wp_loss,
           p.sample_games
    FROM by_pos p JOIN by_move m ON m.epd_before = p.epd_before AND m.rank = 1
    ORDER BY coalesce(p.wp_loss, 0) * p.n DESC, p.n DESC
    LIMIT {int(limit)}
    """
    return fetch_dicts(con, sql, [*params, max_ply, min_occ])
