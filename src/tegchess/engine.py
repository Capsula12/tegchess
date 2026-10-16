"""Evaluación de posiciones: nube de Lichess + Stockfish local en paralelo, con caché por EPD.

Orden de preferencia para cada posición pendiente:
1. Evaluación que ya vino en la partida (Lichess) o que ya está en `evals` → no se recalcula.
2. Posición terminal (mate / ahogado / material insuficiente) → se resuelve sin motor.
3. Nube de Lichess, solo para posiciones de apertura frecuentes (opcional).
4. Stockfish local.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import contextmanager
from typing import Any

import chess
import chess.engine
import duckdb

from tegchess import db
from tegchess.config import Config, find_stockfish
from tegchess.lichess import LichessClient

log = logging.getLogger(__name__)

WRITE_BATCH = 200


def pending_positions(con: duckdb.DuckDBPyConnection, max_ply: int,
                      min_count: int = 1, ply_cap: int | None = None) -> list[tuple[str, int]]:
    """Posiciones (EPD, apariciones) sin evaluar dentro de los primeros `max_ply` plies.

    Ordenadas por frecuencia: si el análisis se corta, ya está lo más útil.
    """
    limit_ply = ply_cap if ply_cap is not None else max_ply
    ply_filter = f"WHERE ply <= {int(limit_ply)}" if limit_ply else ""
    sql = f"""
    WITH pos AS (
        SELECT epd_before AS epd FROM moves {ply_filter}
        UNION ALL
        SELECT epd_after AS epd FROM moves {ply_filter}
    )
    SELECT p.epd, count(*) AS n
    FROM pos p LEFT JOIN evals e ON e.epd = p.epd
    WHERE e.epd IS NULL
    GROUP BY p.epd
    HAVING count(*) >= ?
    ORDER BY n DESC
    """
    return con.execute(sql, [min_count]).fetchall()


def epd_to_fen(epd: str) -> str:
    return epd + " 0 1"


def terminal_eval(board: chess.Board) -> dict[str, Any] | None:
    """Evaluación sin motor de posiciones terminadas."""
    if board.is_checkmate():
        # Le toca mover al que recibió mate.
        return {"cp": -100000 if board.turn == chess.WHITE else 100000, "mate": None}
    if board.is_stalemate() or board.is_insufficient_material():
        return {"cp": 0, "mate": None}
    return None


def _row(epd: str, cp: int | None, mate: int | None, pv: list[str], depth: int | None,
         source: str, lines: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "epd": epd,
        "cp": cp,
        "mate": mate,
        "best_uci": pv[0] if pv else None,
        "pv": " ".join(pv) if pv else None,
        "depth": depth,
        "source": source,
        "lines": json.dumps(lines) if lines else None,
    }


def _score_fields(score: chess.engine.PovScore) -> tuple[int | None, int | None]:
    w = score.white()
    if w.is_mate():
        m = w.mate()
        # mate == 0 no debería aparecer (las terminales se resuelven antes), por si acaso:
        return (None, m) if m else (-100000 if score.turn == chess.WHITE else 100000, None)
    return w.score(), None


# --- nube de Lichess -----------------------------------------------------


def cloud_evals(client: LichessClient, positions: list[str], on_done: Callable[[], None] = lambda: None,
                multi_pv: int = 1) -> Iterator[dict[str, Any]]:
    for epd in positions:
        data = client.cloud_eval(epd_to_fen(epd), multi_pv=multi_pv)
        on_done()
        if not data or not data.get("pvs"):
            continue
        lines = []
        for pv in data["pvs"]:
            moves = pv.get("moves", "").split()
            lines.append({"uci": moves[0] if moves else None, "cp": pv.get("cp"),
                          "mate": pv.get("mate"), "pv": " ".join(moves)})
        best = data["pvs"][0]
        yield _row(epd, best.get("cp"), best.get("mate"), best.get("moves", "").split(),
                   data.get("depth"), "cloud", lines if multi_pv > 1 else None)


# --- Stockfish ---------------------------------------------------------------


class EnginePool:
    """N procesos de Stockfish; cada hilo toma uno libre de la cola."""

    def __init__(self, path: str, workers: int, threads: int = 1, hash_mb: int = 64):
        self.path = path
        self._engines: queue.Queue[chess.engine.SimpleEngine] = queue.Queue()
        self._all: list[chess.engine.SimpleEngine] = []
        for _ in range(workers):
            eng = chess.engine.SimpleEngine.popen_uci(path)
            eng.configure({"Threads": threads, "Hash": hash_mb})
            self._all.append(eng)
            self._engines.put(eng)
        self.size = workers

    @contextmanager
    def engine(self) -> Iterator[chess.engine.SimpleEngine]:
        eng = self._engines.get()
        try:
            yield eng
        finally:
            self._engines.put(eng)

    def close(self) -> None:
        for eng in self._all:
            try:
                eng.quit()
            except Exception:  # el proceso pudo haber muerto (Ctrl+C)
                pass

    def analyse(self, epd: str, depth: int, multipv: int = 1) -> dict[str, Any]:
        board = chess.Board(epd_to_fen(epd))
        with self.engine() as eng:
            infos = eng.analyse(board, chess.engine.Limit(depth=depth), multipv=multipv)
        lines = []
        for info in infos:
            cp, mate = _score_fields(info["score"])
            pv = [m.uci() for m in info.get("pv", [])]
            lines.append({"uci": pv[0] if pv else None, "cp": cp, "mate": mate, "pv": " ".join(pv)})
        best = infos[0]
        cp, mate = _score_fields(best["score"])
        pv = [m.uci() for m in best.get("pv", [])]
        return _row(epd, cp, mate, pv, best.get("depth"), "stockfish", lines if multipv > 1 else None)


def analyse_positions(
    con: duckdb.DuckDBPyConnection,
    pool: EnginePool,
    positions: list[str],
    depth: int,
    multipv: int = 1,
    on_done: Callable[[], None] = lambda: None,
    stop: threading.Event | None = None,
    replace: bool = False,
) -> int:
    """Analiza en paralelo y guarda en lotes desde el hilo principal (DuckDB no es multi-writer)."""
    stop = stop or threading.Event()
    done = 0
    batch: list[dict[str, Any]] = []

    def flush() -> None:
        if batch:
            db.bulk_insert(con, "evals", batch, on_conflict="replace" if replace else "ignore")
            batch.clear()

    it = iter(positions)
    with ThreadPoolExecutor(max_workers=pool.size) as ex:
        running: set[Future] = set()
        try:
            while True:
                while len(running) < pool.size * 2 and not stop.is_set():
                    epd = next(it, None)
                    if epd is None:
                        break
                    running.add(ex.submit(pool.analyse, epd, depth, multipv))
                if not running:
                    break
                finished, running = wait(running, return_when=FIRST_COMPLETED)
                for fut in finished:
                    batch.append(fut.result())
                    done += 1
                    on_done()
                if len(batch) >= WRITE_BATCH:
                    flush()
        finally:
            stop.set()
            for fut in running:
                fut.cancel()
            flush()
    return done


def ensure_best_moves(con: duckdb.DuckDBPyConnection, cfg: Config, epds: list[str], multipv: int = 1) -> int:
    """Garantiza mejor jugada (y multipv) en posiciones puntuales, p.ej. las del informe.

    Las evaluaciones que vienen de Lichess no traen la mejor jugada: esas se recalculan.
    Devuelve cuántas posiciones analizó; 0 si no hay Stockfish.
    """
    if not epds:
        return 0
    cond = "best_uci IS NULL" + (" OR lines IS NULL" if multipv > 1 else "")
    placeholders = ",".join("?" for _ in epds)
    have = {r[0] for r in con.execute(
        f"SELECT epd FROM evals WHERE epd IN ({placeholders}) AND NOT ({cond}) AND source <> 'terminal'", epds
    ).fetchall()}
    terminal = {r[0] for r in con.execute(
        f"SELECT epd FROM evals WHERE epd IN ({placeholders}) AND source = 'terminal'", epds).fetchall()}
    todo = [e for e in dict.fromkeys(epds) if e not in have and e not in terminal]
    path = find_stockfish(cfg.engine.path)
    if not todo or not path:
        return 0
    pool = EnginePool(path, min(cfg.engine.resolved_workers(), len(todo)), cfg.engine.threads_per_worker,
                      cfg.engine.hash_mb)
    try:
        return analyse_positions(con, pool, todo, cfg.engine.depth, multipv=multipv, replace=True)
    finally:
        pool.close()


def evaluate_pending(
    con: duckdb.DuckDBPyConnection,
    cfg: Config,
    client: LichessClient | None = None,
    progress: Callable[[str, int], Callable[[], None]] | None = None,
) -> dict[str, int]:
    """Evalúa todas las posiciones pendientes. Devuelve conteos por origen.

    progress(descripcion, total) -> callback a llamar por cada posición procesada.
    """
    ecfg = cfg.engine
    progress = progress or (lambda _d, _t: (lambda: None))
    stats = {"terminal": 0, "cloud": 0, "stockfish": 0}

    pending = pending_positions(con, ecfg.max_ply)
    if not pending:
        return stats

    # 1) Terminales.
    terminal_rows = []
    for epd, _n in pending:
        board = chess.Board(epd_to_fen(epd))
        if board.is_game_over(claim_draw=False):
            t = terminal_eval(board)
            if t:
                terminal_rows.append(_row(epd, t["cp"], t["mate"], [], None, "terminal"))
    db.bulk_insert(con, "evals", terminal_rows)
    stats["terminal"] = len(terminal_rows)

    # 2) Nube de Lichess para posiciones de apertura repetidas.
    if ecfg.use_cloud_eval and client is not None:
        cloud_pending = [epd for epd, _ in pending_positions(
            con, ecfg.max_ply, min_count=cfg.analysis.min_occurrences, ply_cap=ecfg.cloud_eval_max_ply)]
        if cloud_pending:
            tick = progress("Nube de Lichess", len(cloud_pending))
            batch: list[dict[str, Any]] = []
            for row in cloud_evals(client, cloud_pending, on_done=tick):
                batch.append(row)
                if len(batch) >= WRITE_BATCH:
                    db.bulk_insert(con, "evals", batch)
                    stats["cloud"] += len(batch)
                    batch.clear()
            db.bulk_insert(con, "evals", batch)
            stats["cloud"] += len(batch)

    # 3) Stockfish para el resto.
    remaining = [epd for epd, _ in pending_positions(con, ecfg.max_ply)]
    if remaining:
        path = find_stockfish(ecfg.path)
        if not path:
            raise RuntimeError("No encontré Stockfish. Instalalo (winget install Stockfish.Stockfish) "
                               "o indicá engine.path en config.yaml")
        workers = ecfg.resolved_workers()
        log.info("Stockfish: %d posiciones, profundidad %d, %d procesos", len(remaining), ecfg.depth, workers)
        tick = progress("Stockfish", len(remaining))
        pool = EnginePool(path, workers, ecfg.threads_per_worker, ecfg.hash_mb)
        try:
            stats["stockfish"] = analyse_positions(con, pool, remaining, ecfg.depth, on_done=tick)
        finally:
            pool.close()
    return stats
