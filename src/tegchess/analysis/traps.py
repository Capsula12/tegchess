"""Trampas: posiciones frecuentes donde la jugada habitual del rival es un error.

Para cada posición que el rival repite (≥ min_occurrences) y donde su jugada habitual pierde
mucho según el motor, se mide qué tan fácil es castigarla:
- Motor (multipv 3 en la posición tras su error): si la refutación es una jugada única
  ("precisa") o hay varias que ganan ("fácil"); si es forzante (captura o jaque).
- Empírico: en sus partidas, cuántas veces el rival encontró la refutación y cómo le fue.

Se ordenan por valor esperado: P(llega a la posición con ese color) × P(juega el error) × lo que gano
al castigarlo (puntos de % de victoria, con el peso práctico del motor de expectation.py). Saber la
refutación de memoria vale más si es "precisa", pero igual cuenta un poco menos que una fácil.
"""

from __future__ import annotations

import json
from typing import Any

import chess
import duckdb

from tegchess.analysis.expectation import ENGINE_WEIGHT
from tegchess.analysis.lines import extend_line, line_to_position, pv_san, san
from tegchess.analysis.openings import repeated_positions
from tegchess.analysis.scope import scope_cte
from tegchess.config import Config
from tegchess.engine import ensure_best_moves, epd_to_fen
from tegchess.metrics import eval_cp, win_pct

MIN_LOSS = 8.0          # pérdida mínima (puntos de % de victoria) de su jugada habitual
ONLY_MOVE_GAP = 10.0    # si la 2ª mejor respuesta gana 10 puntos menos, la refutación es "precisa"
CANDIDATES = 300
EASE = {"fácil": 1.0, "precisa": 0.8}
IN_REPERTOIRE_BONUS = 1.5   # si ya llego a esa posición en mis partidas, es más probable provocarla


def _after_epd(con: duckdb.DuckDBPyConnection, epd: str, uci: str) -> str | None:
    row = con.execute("SELECT epd_after FROM moves WHERE epd_before = ? AND uci = ? LIMIT 1", [epd, uci]).fetchone()
    return row[0] if row else None


def _lines(con: duckdb.DuckDBPyConnection, epd: str) -> tuple[list[dict[str, Any]], int | None]:
    row = con.execute("SELECT lines, cp, mate, best_uci, pv FROM evals WHERE epd = ?", [epd]).fetchone()
    if not row:
        return [], None
    lines_json, cp, mate, best, pv = row
    lines = json.loads(lines_json) if lines_json else None
    if isinstance(lines, str):  # filas viejas guardadas con doble codificación
        lines = json.loads(lines)
    lines = lines or [{"uci": best, "cp": cp, "mate": mate, "pv": pv}]
    return lines, eval_cp(cp, mate)


def _empirical(con: duckdb.DuckDBPyConnection, cfg: Config, rival: str, epd: str, usual_uci: str,
               refutation: str | None) -> dict[str, Any]:
    """Qué pasó en sus partidas después de su jugada habitual en esta posición."""
    cte, params = scope_cte(cfg, rival)
    row = con.execute(f"""
        WITH {cte}
        SELECT count(*),
               count(*) FILTER (WHERE n.uci = ?),
               avg(s.score),
               avg(s.score) FILTER (WHERE n.uci = ?),
               avg(s.score) FILTER (WHERE n.uci IS DISTINCT FROM ?)
        FROM scope s
        JOIN moves m ON m.game_id = s.game_id AND m.color = s.color
        LEFT JOIN moves n ON n.game_id = m.game_id AND n.ply = m.ply + 1
        WHERE m.epd_before = ? AND m.uci = ?
    """, [*params, refutation, refutation, refutation, epd, usual_uci]).fetchone()
    total, found, score, score_found, score_missed = row
    pct = lambda x: None if x is None else round(100 * x, 1)  # noqa: E731
    return {
        "games_after_mistake": total,
        "refutation_found": found,
        "rival_score_pct": pct(score),
        "rival_score_when_punished_pct": pct(score_found),
        "rival_score_when_missed_pct": pct(score_missed),
    }


def _my_reach(con: duckdb.DuckDBPyConnection, cfg: Config, me: str, epd: str, my_color: str) -> int:
    """Cuántas partidas mías (con el color que castiga) pasan por la posición."""
    cte, params = scope_cte(cfg, me, my_color)
    return con.execute(f"""
        WITH {cte}
        SELECT count(DISTINCT s.game_id) FROM scope s JOIN moves m ON m.game_id = s.game_id
        WHERE m.epd_before = ?
    """, [*params, epd]).fetchone()[0]


def find_traps(con: duckdb.DuckDBPyConnection, cfg: Config, rival: str, me: str | None = None,
               limit: int = 15) -> list[dict[str, Any]]:
    def candidates() -> list[dict[str, Any]]:
        rows = repeated_positions(con, cfg, rival, limit=CANDIDATES)
        return [r for r in rows if (r["usual_wp_loss"] or 0) >= MIN_LOSS and r["usual_uci"]]

    cands = candidates()
    # Mejor jugada en la posición del rival y multipv en la posición tras su error.
    after = {(r["epd"], r["usual_uci"]): _after_epd(con, r["epd"], r["usual_uci"]) for r in cands}
    changed = ensure_best_moves(con, cfg, [r["epd"] for r in cands])
    changed += ensure_best_moves(con, cfg, [a for a in after.values() if a], multipv=3)
    if changed:
        cands = candidates()

    cte, params = scope_cte(cfg, rival)
    games_by_color = dict(con.execute(f"WITH {cte} SELECT color, count(*) FROM scope GROUP BY 1", params).fetchall())

    traps = []
    for r in cands:
        epd, usual = r["epd"], r["usual_uci"]
        after_epd = after.get((epd, usual)) or _after_epd(con, epd, usual)
        if not after_epd:
            continue
        lines, _ = _lines(con, after_epd)
        if not lines or not lines[0].get("uci"):
            continue
        punisher = "black" if r["color"] == "white" else "white"
        sign = 1 if punisher == "white" else -1
        wps = [win_pct(sign * eval_cp(line.get("cp"), line.get("mate"))) for line in lines
               if eval_cp(line.get("cp"), line.get("mate")) is not None]
        refutation = lines[0]["uci"]
        board = chess.Board(epd_to_fen(after_epd))
        move = chess.Move.from_uci(refutation)
        forcing = move in board.legal_moves and (board.is_capture(move) or board.gives_check(move))
        only_move = len(wps) >= 2 and wps[0] - wps[1] >= ONLY_MOVE_GAP
        winning_moves = sum(1 for w in wps if w >= 60)

        emp = _empirical(con, cfg, rival, epd, usual, refutation)
        my_games = _my_reach(con, cfg, me, epd, punisher) if me else 0

        reach = r["occurrences"] / games_by_color.get(r["color"], 0) if games_by_color.get(r["color"]) else 0
        err = r["usual_count"] / r["occurrences"]
        difficulty = "fácil" if forcing or not only_move else "precisa"
        gain = ENGINE_WEIGHT * (r["usual_wp_loss"] or 0)

        line = line_to_position(con, epd)
        pv_moves = (lines[0].get("pv") or refutation).split()
        full = extend_line(line, epd, [usual, *pv_moves[:3]])
        traps.append({
            "epd": epd,
            "fen": epd_to_fen(epd),
            "ply": r["ply"],
            "rival_color": r["color"],
            "punisher_color": punisher,
            "opening": r["opening"],
            "occurrences": r["occurrences"],
            "usual_san": r["usual_san"],
            "usual_uci": usual,
            "usual_count": r["usual_count"],
            "usual_wp_loss": r["usual_wp_loss"],
            "best_san": san(epd, r["best_uci"]),
            "best_uci": r["best_uci"],
            "refutation_san": san(after_epd, refutation),
            "refutation_uci": refutation,
            "refutation_line": pv_san(after_epd, lines[0].get("pv"), ply_before=r["ply"]),
            "punisher_win_pct": round(wps[0], 1) if wps else None,
            "forcing": forcing,
            "only_move": only_move,
            "winning_moves": winning_moves,
            "difficulty": difficulty,
            "reach_pct": round(100 * min(1.0, reach), 1),
            "error_pct": round(100 * err, 1),
            "gain_pct": round(gain, 1),
            # Puntos de % que suma, en promedio, tener preparada esta trampa para una partida contra él.
            "expected_value_pct": round(min(1.0, reach) * err * gain * EASE[difficulty], 2),
            **emp,
            "my_games": my_games,
            "in_my_repertoire": my_games > 0,
            "line": full,
            "line_mistake_index": len(line),   # índice en `line` de la jugada del rival
        })

    def priority(t: dict[str, Any]) -> float:
        return t["expected_value_pct"] * (IN_REPERTOIRE_BONUS if t["in_my_repertoire"] else 1.0)

    traps.sort(key=priority, reverse=True)
    return traps[:limit]
