"""Analizar una posición suelta con el Stockfish local: evaluación y las mejores jugadas con su variante."""

from __future__ import annotations

from typing import Any

import chess
import chess.engine

from tegchess.config import Config, find_stockfish
from tegchess.metrics import eval_cp

DEFAULT_DEPTH = 22
MAX_SECONDS = 6.0


def validate(fen: str) -> chess.Board:
    """Tablero listo para analizar, o ValueError con el motivo en castellano."""
    try:
        board = chess.Board(fen)
    except ValueError as e:
        raise ValueError(f"FEN inválido: {e}") from e
    status = board.status()
    problems = {
        chess.STATUS_NO_WHITE_KING: "falta el rey blanco", chess.STATUS_NO_BLACK_KING: "falta el rey negro",
        chess.STATUS_TOO_MANY_KINGS: "hay más de un rey de un color",
        chess.STATUS_PAWNS_ON_BACKRANK: "hay peones en la primera u octava fila",
        chess.STATUS_OPPOSITE_CHECK: "el bando que no mueve está en jaque (¿el turno está al revés?)",
        chess.STATUS_TOO_MANY_WHITE_PIECES: "demasiadas piezas blancas",
        chess.STATUS_TOO_MANY_BLACK_PIECES: "demasiadas piezas negras",
    }
    found = [msg for flag, msg in problems.items() if status & flag]
    if found:
        raise ValueError("La posición no es válida: " + "; ".join(found))
    return board


def analyse(cfg: Config, fen: str, multipv: int = 3, depth: int = DEFAULT_DEPTH) -> dict[str, Any]:
    board = validate(fen)
    if board.is_game_over():
        result = "mate" if board.is_checkmate() else "tablas"
        return {"fen": board.fen(), "game_over": result, "lines": []}
    path = find_stockfish(cfg.engine.path)
    if not path:
        raise RuntimeError("No encontré Stockfish (instalalo con: winget install Stockfish.Stockfish)")
    engine = chess.engine.SimpleEngine.popen_uci(path)
    try:
        engine.configure({"Threads": max(1, cfg.engine.resolved_workers()), "Hash": 128})
        infos = engine.analyse(board, chess.engine.Limit(depth=depth, time=MAX_SECONDS), multipv=multipv)
    finally:
        engine.quit()
    lines = []
    for rank, info in enumerate(infos, 1):
        pv = info.get("pv") or []
        if not pv:
            continue
        score = info["score"].white()
        steps, b = [], board.copy()
        for move in pv[:10]:
            steps.append({"san": b.san(move), "uci": move.uci()})
            b.push(move)
            steps[-1]["fen"] = b.fen()
        lines.append({
            "steps": steps,
            "rank": rank,
            "uci": pv[0].uci(),
            "san": board.san(pv[0]),
            "cp": eval_cp(score.score(), score.mate()),
            "mate": score.mate(),
            "pv_san": board.variation_san(pv[:10]),
            "pv_uci": [m.uci() for m in pv[:10]],
            "depth": info.get("depth"),
        })
    return {"fen": board.fen(), "turn": "w" if board.turn else "b", "lines": lines, "game_over": None}
