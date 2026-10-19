"""Preparación contra un rival concreto: qué juega casi siempre y qué líneas vale la pena memorizar.

Las partidas de Lichess no son preparadas, pero una partida contra un rival conocido sí puede serlo.
La señal que sirve para preparar es estable: las jugadas que el rival repite y la evaluación del motor
en las posiciones a las que llega (el resultado por apertura, en cambio, es ruido; ver expectation.py).

Se recorre el árbol de sus partidas con el color que va a jugar:
- En sus jugadas, cada rama pesa lo que él la elige (probabilidad acumulada = qué tan seguido llega).
- En las jugadas del otro lado elegimos nosotros: se siguen las que sus rivales jugaron contra él,
  así hay datos de cómo responde.
Un "objetivo" es una posición tras su jugada en la que quedo mejor de lo normal para mi color según el motor
(la referencia es la posición inicial: con negras, igualar ya es ganar terreno), o en la que su jugada habitual
empeora claramente su posición. Su valor es probabilidad de llegar × ventaja (en puntos de %, con el mismo peso
del motor que la expectativa).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import chess
import duckdb

from tegchess.analysis.expectation import ENGINE_WEIGHT
from tegchess.analysis.scope import scope_cte
from tegchess.config import Config
from tegchess.metrics import eval_cp, win_pct

MAX_PLY = 20            # hasta dónde se prepara (10 jugadas)
MIN_GAMES = 3           # partidas suyas en la posición para seguir la rama
MIN_MOVE_GAMES = 3      # veces que tiene que haber jugado (o enfrentado) una jugada para seguirla
MIN_SHARE = 0.15        # elección suya mínima para seguir una rama
MIN_REACH = 0.08        # probabilidad acumulada mínima
MY_BRANCHES = 4         # respuestas nuestras que se exploran en cada posición
MIN_GAIN = 3.0          # ventaja mínima sobre lo normal para mi color (puntos de %, ~ +50 cp)
MIN_SWING = 4.0         # o su jugada empeora su posición al menos esto (puntos de %)
START_EVAL_CP = 25      # evaluación típica de la posición inicial (si no está en la base)
MAIN_LINE_REACH = 0.25  # la "línea principal" se sigue mientras la probabilidad sea al menos esta
SHARE_PRIOR = 2         # partidas ficticias "en otra jugada": 3 de 3 no es 100% sino 60%


def _rival_tree(con: duckdb.DuckDBPyConnection, cfg: Config, rival: str, color: str
                ) -> tuple[dict[str, int], dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]], int]:
    """(partidas por posición, sus jugadas por posición, jugadas de sus rivales por posición, total de partidas)."""
    cte, params = scope_cte(cfg, rival, color)
    reach = dict(con.execute(f"""
        WITH {cte}
        SELECT m.epd_before, count(DISTINCT s.game_id) FROM scope s JOIN moves m ON m.game_id = s.game_id
        WHERE m.ply <= {MAX_PLY + 1} GROUP BY 1
    """, params).fetchall())
    total = con.execute(f"WITH {cte} SELECT count(*) FROM scope", params).fetchone()[0]
    rows = con.execute(f"""
        WITH {cte}
        SELECT m.epd_before, m.uci, any_value(m.san), any_value(m.epd_after), count(*), m.color = s.color AS his,
               avg(s.score)
        FROM scope s JOIN moves m ON m.game_id = s.game_id
        WHERE m.ply <= {MAX_PLY}
        GROUP BY m.epd_before, m.uci, his
    """, params).fetchall()
    his: dict[str, list[dict[str, Any]]] = defaultdict(list)
    theirs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for epd, uci, san, after, n, is_his, score in rows:
        (his if is_his else theirs)[epd].append({"uci": uci, "san": san, "epd_after": after, "n": n,
                                                 "score": score})
    for d in (his, theirs):
        for lst in d.values():
            lst.sort(key=lambda m: (-m["n"], m["uci"]))
    return reach, his, theirs, total


def _my_moves(con: duckdb.DuckDBPyConnection, cfg: Config, me: str, color: str) -> dict[tuple[str, str], int]:
    cte, params = scope_cte(cfg, me, color)
    return {(e, u): n for e, u, n in con.execute(f"""
        WITH {cte}
        SELECT m.epd_before, m.uci, count(*) FROM scope s
        JOIN moves m ON m.game_id = s.game_id AND m.color = s.color
        WHERE m.ply <= {MAX_PLY} GROUP BY 1, 2
    """, params).fetchall()}


def _evals(con: duckdb.DuckDBPyConnection, epds: set[str]) -> dict[str, tuple[int | None, str | None]]:
    out: dict[str, tuple[int | None, str | None]] = {}
    lst = list(epds)
    for i in range(0, len(lst), 1000):
        chunk = lst[i:i + 1000]
        for epd, cp, mate, best in con.execute(
            f"SELECT epd, cp, mate, best_uci FROM evals WHERE epd IN ({','.join('?' for _ in chunk)})", chunk
        ).fetchall():
            out[epd] = (eval_cp(cp, mate), best)
    return out


def prep_section(con: duckdb.DuckDBPyConnection, cfg: Config, rival: str, my_color: str,
                 me: str | None = None, limit: int = 8) -> dict[str, Any]:
    """Objetivos de preparación y línea principal del rival cuando yo juego con my_color."""
    riv_color = "black" if my_color == "white" else "white"
    reach, his, theirs, total = _rival_tree(con, cfg, rival, riv_color)
    if not total:
        return {"games": 0, "targets": [], "main_line": [], "predictability_pct": None}
    mine = _my_moves(con, cfg, me, my_color) if me else {}
    evals = _evals(con, set(reach) | {m["epd_after"] for d in (his, theirs) for ms in d.values() for m in ms})
    sign = 1 if my_color == "white" else -1

    def eval_me(epd: str) -> float | None:
        e = evals.get(epd, (None, None))[0]
        return None if e is None else sign * e

    # Lo normal para mi color: la evaluación de la posición inicial vista desde mí.
    start = eval_me(chess.Board().epd())
    baseline = win_pct(start if start is not None else sign * START_EVAL_CP)

    targets: list[dict[str, Any]] = []

    def walk(board: chess.Board, line: list[dict[str, Any]], prob: float) -> None:
        epd = board.epd()
        n = reach.get(epd, 0)
        if n < MIN_GAMES or len(line) >= MAX_PLY or prob < MIN_REACH:
            return
        his_turn = (board.turn == chess.WHITE) == (riv_color == "white")
        if his_turn:
            options = his.get(epd, [])
            moved = sum(m["n"] for m in options) + SHARE_PRIOR
            e_before = eval_me(epd)
            for m in options:
                share = m["n"] / moved
                if share < MIN_SHARE or m["n"] < MIN_MOVE_GAMES:
                    continue
                move = chess.Move.from_uci(m["uci"])
                if move not in board.legal_moves:
                    continue
                e_after = eval_me(m["epd_after"])
                child_prob = prob * share
                step = {"san": m["san"], "uci": m["uci"], "share_pct": round(100 * share)}
                board.push(move)
                step["fen"] = board.fen()
                new_line = [*line, step]
                if e_after is not None:
                    gain = ENGINE_WEIGHT * (win_pct(e_after) - baseline)
                    # Su error en esta jugada (cuánto empeora la posición para él).
                    swing = None if e_before is None else ENGINE_WEIGHT * (win_pct(e_after) - win_pct(e_before))
                    good = gain >= MIN_GAIN or (gain > 0 and swing is not None and swing >= MIN_SWING)
                    if good and child_prob >= MIN_REACH:
                        my_steps = [s for s in new_line if s.get("mine") is not None]
                        targets.append({
                            "epd": m["epd_after"],
                            "line": new_line,
                            "reach_pct": round(100 * child_prob, 1),
                            "eval_for_me": round(e_after),
                            "gain_pct": round(gain, 1),
                            "value": round(child_prob * gain, 2),
                            "rival_mistake_pct": None if swing is None else round(swing, 1),
                            "my_moves": len(my_steps),
                            "known_moves": sum(1 for s in my_steps if s["mine"]),
                            "rival_games": m["n"],
                            "best_reply_uci": evals.get(m["epd_after"], (None, None))[1],
                        })
                walk(board, new_line, child_prob)
                board.pop()
        else:
            options = theirs.get(epd, [])
            # Las más jugadas contra él y, además, las de mi repertorio (si él tiene partidas ahí).
            branches = options[:MY_BRANCHES] + [m for m in options[MY_BRANCHES:] if mine.get((epd, m["uci"]), 0) >= 2]
            for m in branches:
                if m["n"] < MIN_MOVE_GAMES:
                    continue
                move = chess.Move.from_uci(m["uci"])
                if move not in board.legal_moves:
                    continue
                board.push(move)
                step = {"san": m["san"], "uci": m["uci"], "fen": board.fen(), "mine": mine.get((epd, m["uci"]), 0),
                        "vs_games": m["n"]}
                walk(board, [*line, step], prob)
                board.pop()

    walk(chess.Board(), [], 1.0)

    # Mejores objetivos sin repetir posición y sin líneas que son prefijo de otra mejor.
    # Lo que ya juego pesa un poco más: hay menos para memorizar.
    targets.sort(key=lambda t: t["value"] * (1.25 if t["my_moves"] and t["known_moves"] == t["my_moves"] else 1),
                 reverse=True)
    chosen: list[dict[str, Any]] = []
    seen: set[str] = set()
    paths: list[tuple[str, ...]] = []
    for t in targets:
        path = tuple(st["uci"] for st in t["line"])
        # Misma rama que uno ya elegido (una es prefijo de la otra): ya está cubierto.
        if t["epd"] in seen or any(p[:len(path)] == path or path[:len(p)] == p for p in paths):
            continue
        seen.add(t["epd"])
        paths.append(path)
        chosen.append(t)
        if len(chosen) >= limit:
            break
    for t in chosen:
        t["in_my_repertoire"] = t["my_moves"] > 0 and t["known_moves"] == t["my_moves"]
        t["kind"] = "trampa" if (t["rival_mistake_pct"] or 0) >= MIN_SWING else "ventaja"
        t["memorize_plies"] = len(t["line"])

    return {"games": total, "targets": chosen, **_main_line(reach, his, theirs, riv_color)}


def _main_line(reach: dict[str, int], his: dict[str, list[dict[str, Any]]],
               theirs: dict[str, list[dict[str, Any]]], riv_color: str) -> dict[str, Any]:
    """Su línea más jugada: la jugada más frecuente de cada lado mientras la probabilidad no baje de MAIN_LINE_REACH."""
    board = chess.Board()
    line: list[dict[str, Any]] = []
    prob = 1.0
    shares: list[float] = []
    while len(line) < MAX_PLY:
        epd = board.epd()
        if reach.get(epd, 0) < MIN_GAMES:
            break
        his_turn = (board.turn == chess.WHITE) == (riv_color == "white")
        options = (his if his_turn else theirs).get(epd, [])
        if not options:
            break
        top = options[0]
        share = top["n"] / (sum(m["n"] for m in options) + (SHARE_PRIOR if his_turn else 0))
        if his_turn:
            if prob * share < MAIN_LINE_REACH:
                break
            prob *= share
            shares.append(share)
        move = chess.Move.from_uci(top["uci"])
        if move not in board.legal_moves:
            break
        board.push(move)
        line.append({"san": top["san"], "uci": top["uci"], "fen": board.fen(), "his": his_turn,
                     "share_pct": round(100 * share), "reach_pct": round(100 * prob, 1)})
    return {
        "main_line": line,
        "main_line_reach_pct": round(100 * prob, 1) if line else None,
        # Qué tan predecible es: elección media de su jugada más frecuente en la línea principal.
        "predictability_pct": round(100 * sum(shares) / len(shares)) if shares else None,
    }
