"""Modo PGN: importa las partidas de los archivos de una carpeta (p.ej. exportadas de ChessBase).

- La carpeta manda: si un archivo cambia se reimporta, si desaparece se borran sus partidas.
- Los jugadores se identifican por el nombre normalizado ("perez, juan"); el Elo es el de los tags.
- Sin división de fases de Lichess: se calcula con una versión simple de su criterio (material).
- Sin nombre de apertura en el PGN: se toma de las partidas de Lichess que pasan por la misma posición.
- Si el PGN trae relojes ([%clk]) o evaluaciones ([%eval]), se aprovechan.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import chess
import chess.pgn
import duckdb

from tegchess.config import Config, normalize_player
from tegchess.ingest import ParsedGame, epd, store_games

log = logging.getLogger(__name__)

BATCH_SIZE = 500
PLAYERS_FILE = "pgn_players.json"


# --- partida -> filas -------------------------------------------------------------


def _int(v: str | None) -> int | None:
    try:
        n = int(v or "")
        return n if n > 0 else None
    except ValueError:
        return None


def _date(headers: chess.pgn.Headers) -> str | None:
    """'2019.05.12' -> '2019-05-12 00:00:00'; los '??' se completan con 01."""
    raw = headers.get("Date") or headers.get("UTCDate") or ""
    m = re.match(r"(\d{4})\.([\d?]{2})\.([\d?]{2})", raw)
    if not m:
        return None
    y, mo, d = m.groups()
    mo = "01" if "?" in mo else mo
    d = "01" if "?" in d else d
    return f"{y}-{mo}-{d} 00:00:00"


def _perf(headers: chess.pgn.Headers) -> str:
    """Modalidad según TimeControl o el nombre del torneo; por defecto, clásica."""
    tc = headers.get("TimeControl", "")
    m = re.match(r"(\d+)(?:\+(\d+))?", tc)
    if m:
        base, inc = int(m.group(1)), int(m.group(2) or 0)
        total = base + 40 * inc
        return "blitz" if total < 600 else "rapid" if total < 3600 else "classical"
    text = " ".join([headers.get("EventType", ""), headers.get("Event", "")]).lower()
    if "blitz" in text or "bullet" in text:
        return "blitz"
    if "rapid" in text or "rápid" in text:
        return "rapid"
    return "classical"


def _clock(headers: chess.pgn.Headers) -> tuple[int | None, int | None]:
    m = re.match(r"(\d+)(?:\+(\d+))?", headers.get("TimeControl", ""))
    return (int(m.group(1)), int(m.group(2) or 0)) if m else (None, None)


def _majors_minors(board: chess.Board) -> int:
    return sum(len(board.pieces(pt, c)) for pt in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN)
               for c in (chess.WHITE, chess.BLACK))


def _backrank_sparse(board: chess.Board) -> bool:
    white = sum(1 for sq in chess.SquareSet(chess.BB_RANK_1) if board.piece_at(sq) and board.piece_at(sq).color)
    black = sum(1 for sq in chess.SquareSet(chess.BB_RANK_8)
                if board.piece_at(sq) and not board.piece_at(sq).color)
    return white < 4 or black < 4


def game_id(headers: chess.pgn.Headers, movetext: str) -> str:
    key = "|".join([headers.get(k, "") for k in ("White", "Black", "Date", "Event", "Round", "Result")] + [movetext])
    return "pgn_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def parse_pgn_game(game: chess.pgn.Game, file_name: str) -> ParsedGame | None:
    h = game.headers
    if h.get("Variant", "Standard").lower() not in ("standard", "chess", ""):
        return None
    board = game.board()
    if board.fen() != chess.STARTING_FEN:
        return None   # posiciones iniciales distintas no aportan al repertorio
    sans: list[str] = []
    moves: list[dict[str, Any]] = []
    evals: list[dict[str, Any]] = []
    init_s, inc_s = _clock(h)
    clocks: list[int | None] = []
    div_middle = div_end = None
    for i, node in enumerate(game.mainline()):
        move = node.move
        if move not in board.legal_moves:
            log.warning("Jugada ilegal en %s (%s - %s), ply %d", file_name, h.get("White"), h.get("Black"), i + 1)
            break
        before = epd(board)
        color = "white" if board.turn == chess.WHITE else "black"
        piece = board.piece_at(move.from_square)
        is_capture = board.is_capture(move)
        san = board.san(move)
        board.push(move)
        after = epd(board)
        sans.append(san)
        clk = node.clock()
        clock_cs = int(clk * 100) if clk is not None else None
        clocks.append(clock_cs)
        spent_cs = None
        if clock_cs is not None:
            prev = clocks[i - 2] if i >= 2 else (init_s * 100 if init_s else None)
            if prev is not None:
                spent_cs = max(0, prev - clock_cs + (inc_s or 0) * 100)
        moves.append({"ply": i + 1, "color": color, "san": san, "uci": move.uci(), "epd_before": before,
                      "epd_after": after, "clock_cs": clock_cs, "spent_cs": spent_cs, "is_capture": is_capture,
                      "is_check": board.is_check(), "piece": piece.symbol().lower() if piece else None})
        ev = node.eval()
        if ev is not None:
            w = ev.white()
            evals.append({"epd": after, "cp": w.score(), "mate": w.mate(), "best_uci": None, "pv": None,
                          "depth": None, "source": "pgn"})
        # División de fases (versión simple del criterio de Lichess).
        mm = _majors_minors(board)
        if div_middle is None and (mm <= 10 or _backrank_sparse(board)):
            div_middle = i + 1
        if div_end is None and div_middle is not None and mm <= 6:
            div_end = i + 1

    movetext = " ".join(sans)
    gid = game_id(h, movetext)
    for m in moves:
        m["game_id"] = gid
    result = h.get("Result", "*")
    winner = "white" if result == "1-0" else "black" if result == "0-1" else None
    if result == "*":
        return None   # partidas sin terminar
    status = ("mate" if board.is_checkmate() else "stalemate" if board.is_stalemate()
              else "draw" if winner is None else "resign")
    white, black = h.get("White", "?"), h.get("Black", "?")
    opening = h.get("Opening")
    if opening and h.get("Variation"):
        opening = f"{opening}: {h['Variation']}"
    created = _date(h)
    return ParsedGame(game={
        "id": gid, "rated": True, "variant": "standard", "speed": _perf(h), "perf": _perf(h),
        "created_at": created, "last_move_at": created, "status": status, "winner": winner,
        "white": normalize_player(white), "black": normalize_player(black),
        "white_name": white.strip(), "black_name": black.strip(),
        "white_rating": _int(h.get("WhiteElo")), "black_rating": _int(h.get("BlackElo")),
        "white_title": h.get("WhiteTitle"), "black_title": h.get("BlackTitle"),
        "eco": h.get("ECO"), "opening_name": opening,
        "opening_family": opening.split(":")[0].strip() if opening else None, "opening_ply": None,
        "clock_initial": init_s, "clock_increment": inc_s, "div_middle": div_middle, "div_end": div_end,
        "num_plies": len(sans), "has_analysis": bool(evals), "white_accuracy": None, "black_accuracy": None,
        "source": h.get("Event"), "origin": "pgn", "pgn_file": file_name,
    }, moves=moves, evals=evals)


# --- carpeta -> base ----------------------------------------------------------------


def _delete_file_games(con: duckdb.DuckDBPyConnection, file_name: str) -> None:
    con.execute("DELETE FROM moves WHERE game_id IN (SELECT id FROM games WHERE origin = 'pgn' AND pgn_file = ?)",
                [file_name])
    con.execute("DELETE FROM games WHERE origin = 'pgn' AND pgn_file = ?", [file_name])
    con.execute("DELETE FROM pgn_files WHERE path = ?", [file_name])


def import_folder(con: duckdb.DuckDBPyConnection, cfg: Config,
                  progress: Callable[[str, int | None], Callable[[], None]] = lambda _d, _t: (lambda: None)
                  ) -> dict[str, Any]:
    """Sincroniza la base con los PGN de la carpeta. Devuelve un resumen por archivo."""
    folder = cfg.pgn_path
    folder.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in folder.rglob("*") if p.suffix.lower() == ".pgn" and p.is_file())
    current = {str(p.relative_to(folder)): p for p in files}
    known = {path: (size, mtime) for path, size, mtime in
             con.execute("SELECT path, size, mtime FROM pgn_files").fetchall()}

    summary: dict[str, Any] = {"folder": str(folder), "files": {}, "removed": []}
    for name in known.keys() - current.keys():
        _delete_file_games(con, name)
        summary["removed"].append(name)

    for name, path in current.items():
        st = path.stat()
        if known.get(name) == (st.st_size, st.st_mtime):
            summary["files"][name] = "sin cambios"
            continue
        if name in known:
            _delete_file_games(con, name)
        n = _import_file(con, path, name, progress)
        con.execute("INSERT OR REPLACE INTO pgn_files (path, size, mtime, games) VALUES (?, ?, ?, ?)",
                    [name, st.st_size, st.st_mtime, n])
        summary["files"][name] = n

    name_openings(con)
    summary["players"] = write_players_index(con, cfg)
    return summary


def _import_file(con: duckdb.DuckDBPyConnection, path: Path, name: str,
                 progress: Callable[[str, int | None], Callable[[], None]]) -> int:
    tick = progress(f"Importando {name}", None)
    batch: list[ParsedGame] = []
    seen: set[str] = set()
    count = 0
    # ChessBase exporta en latin-1 a veces: se prueba UTF-8 y, si falla, latin-1.
    encoding = "utf-8"
    try:
        path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        encoding = "latin-1"
    with open(path, encoding=encoding, errors="replace") as f:
        while True:
            try:
                game = chess.pgn.read_game(f)
            except Exception as e:  # noqa: BLE001 - un PGN roto no debe frenar el resto
                log.warning("Partida ilegible en %s: %s", name, e)
                continue
            if game is None:
                break
            parsed = parse_pgn_game(game, name)
            if parsed is None or parsed.game["id"] in seen:
                continue
            seen.add(parsed.game["id"])
            batch.append(parsed)
            count += 1
            tick()
            if len(batch) >= BATCH_SIZE:
                store_games(con, batch)
                batch.clear()
    store_games(con, batch)
    return count


def name_openings(con: duckdb.DuckDBPyConnection) -> int:
    """Pone nombre de apertura a las partidas PGN que no lo traen.

    Usa la posición más profunda (hasta el ply 30) que tenga nombre en alguna partida de Lichess
    (Lichess nombra la apertura en la posición `opening_ply`).
    """
    con.execute("""
        CREATE OR REPLACE TEMP TABLE known_openings AS
        SELECT m.epd_after AS epd, mode(g.opening_name) AS name, any_value(g.eco) AS eco
        FROM games g JOIN moves m ON m.game_id = g.id AND m.ply = g.opening_ply
        WHERE g.origin = 'lichess' AND g.opening_name IS NOT NULL
        GROUP BY 1
    """)
    rows = con.execute("""
        WITH cand AS (
            SELECT g.id, arg_max(k.name, m.ply) AS name, arg_max(k.eco, m.ply) AS eco, max(m.ply) AS ply
            FROM games g JOIN moves m ON m.game_id = g.id AND m.ply <= 30
            JOIN known_openings k ON k.epd = m.epd_after
            WHERE g.origin = 'pgn' AND g.opening_name IS NULL
            GROUP BY g.id
        )
        UPDATE games SET opening_name = cand.name, opening_family = split_part(cand.name, ':', 1),
                         opening_ply = cand.ply, eco = coalesce(games.eco, cand.eco)
        FROM cand WHERE games.id = cand.id
    """).fetchone()
    # Las que siguen sin nombre: por código ECO, así al menos se agrupan.
    con.execute("""
        UPDATE games SET opening_name = 'ECO ' || eco, opening_family = 'ECO ' || substr(eco, 1, 2)
        WHERE origin = 'pgn' AND opening_name IS NULL AND eco IS NOT NULL
    """)
    return rows[0] if rows else 0


def write_players_index(con: duckdb.DuckDBPyConnection, cfg: Config) -> int:
    """data/pgn_players.json: jugadores de los PGN con partidas, Elo y fechas (para el buscador)."""
    rows = con.execute("""
        WITH p AS (
            SELECT white AS id, white_name AS name, white_rating AS rating, created_at, 'white' AS color
            FROM games WHERE origin = 'pgn'
            UNION ALL
            SELECT black, black_name, black_rating, created_at, 'black' FROM games WHERE origin = 'pgn'
        )
        SELECT id, mode(name), count(*), count(*) FILTER (WHERE color = 'white'),
               arg_max(rating, created_at), min(created_at), max(created_at)
        FROM p GROUP BY id ORDER BY count(*) DESC, id
    """).fetchall()
    players = [{"id": pid, "name": name, "games": n, "white": w, "rating": r,
                "first": str(first)[:10] if first else None, "last": str(last)[:10] if last else None}
               for pid, name, n, w, r, first, last in rows]
    path = cfg.data_path / PLAYERS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(players, ensure_ascii=False), encoding="utf-8")
    return len(players)


def search_players(cfg: Config, query: str = "", limit: int = 30) -> list[dict[str, Any]]:
    """Busca en el índice de jugadores (sin abrir la base: puede estar ocupada por una tarea)."""
    path = cfg.data_path / PLAYERS_FILE
    if not path.exists():
        return []
    players = json.loads(path.read_text(encoding="utf-8"))
    words = [w for w in re.split(r"[\s,]+", _fold(query)) if w]
    if words:
        players = [p for p in players if all(w in _fold(p["name"]) for w in words)]
    return players[:limit]


def _fold(text: str) -> str:
    """Minúsculas y sin tildes, para buscar "perez" y encontrar "Pérez"."""
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")

