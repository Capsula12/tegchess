"""Utilidades para mostrar posiciones: línea de jugadas para llegar, SAN de una jugada UCI."""

from __future__ import annotations

import chess
import duckdb

from tegchess.engine import epd_to_fen


def line_to(con: duckdb.DuckDBPyConnection, game_id: str, ply: int) -> list[dict[str, str]]:
    """Jugadas de una partida hasta antes del `ply`, con el FEN tras cada una para el tablero."""
    rows = con.execute(
        "SELECT san, uci FROM moves WHERE game_id = ? AND ply < ? ORDER BY ply", [game_id, ply]
    ).fetchall()
    board = chess.Board()
    line = []
    for san, uci in rows:
        board.push_uci(uci)
        line.append({"san": san, "uci": uci, "fen": board.fen()})
    return line


def line_to_position(con: duckdb.DuckDBPyConnection, epd: str,
                     game_ids: list[str] | None = None) -> list[dict[str, str]]:
    """Línea más corta (entre las partidas guardadas, o las indicadas) que llega a la posición."""
    sql = "SELECT game_id, ply FROM moves WHERE epd_before = ?"
    params: list = [epd]
    if game_ids:
        sql += f" AND game_id IN ({','.join('?' for _ in game_ids)})"
        params += game_ids
    row = con.execute(sql + " ORDER BY ply, game_id LIMIT 1", params).fetchone()
    return line_to(con, row[0], row[1]) if row else []


def extend_line(line: list[dict[str, str]], start_epd: str, ucis: list[str]) -> list[dict[str, str]]:
    """Agrega jugadas UCI (p.ej. la jugada del rival y la refutación) a una línea."""
    board = chess.Board(line[-1]["fen"]) if line else chess.Board(epd_to_fen(start_epd))
    out = list(line)
    for uci in ucis:
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            break
        san = board.san(move)
        board.push(move)
        out.append({"san": san, "uci": uci, "fen": board.fen()})
    return out


def san(epd: str, uci: str | None) -> str | None:
    if not uci:
        return None
    board = chess.Board(epd_to_fen(epd))
    try:
        move = chess.Move.from_uci(uci)
        return board.san(move) if move in board.legal_moves else None
    except ValueError:
        return None


def pv_san(epd: str, pv: str | None, ply_before: int = 0, max_moves: int = 6) -> str | None:
    """Variante principal en SAN con la numeración real (p.ej. '12. Nxe5 dxe5 13. Qh5').

    ply_before: plies jugados antes de la posición (para numerar bien).
    """
    if not pv:
        return None
    board = chess.Board(epd_to_fen(epd))
    board.fullmove_number = ply_before // 2 + 1
    start = board.copy()
    moves = []
    for uci in pv.split()[:max_moves]:
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            break
        moves.append(move)
        board.push(move)
    return start.variation_san(moves) if moves else None


def opening_at(con: duckdb.DuckDBPyConnection, epd: str) -> str | None:
    """Nombre de apertura más común entre las partidas que pasan por la posición."""
    row = con.execute("""
        SELECT mode(g.opening_name) FROM moves m JOIN games g ON g.id = m.game_id WHERE m.epd_before = ?
    """, [epd]).fetchone()
    return row[0] if row else None
