"""Convierte partidas de Lichess (JSON) en filas y las sincroniza con DuckDB."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import chess
import duckdb

from tegchess import db
from tegchess.config import Config
from tegchess.lichess import LichessClient, StreamInterrupted, to_millis

log = logging.getLogger(__name__)

BATCH_SIZE = 200


def epd(board: chess.Board) -> str:
    """Clave de posición: FEN sin contadores (en passant solo si es legal, como python-chess)."""
    return board.epd()


@dataclass
class ParsedGame:
    game: dict[str, Any]
    moves: list[dict[str, Any]] = field(default_factory=list)
    evals: list[dict[str, Any]] = field(default_factory=list)


def _ts(ms: int | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _player(raw: dict[str, Any], color: str) -> dict[str, Any]:
    p = raw.get("players", {}).get(color, {})
    user = p.get("user") or {}
    name = user.get("name") or (f"AI nivel {p['aiLevel']}" if "aiLevel" in p else "anónimo")
    return {
        "id": (user.get("id") or name).lower(),
        "name": name,
        "rating": p.get("rating"),
        "title": user.get("title"),
        "accuracy": (p.get("analysis") or {}).get("accuracy"),
    }


def parse_game(raw: dict[str, Any]) -> ParsedGame:
    """Parsea una partida NDJSON de Lichess. Las jugadas ilegales cortan el parseo (se loguea)."""
    w, b = _player(raw, "white"), _player(raw, "black")
    opening = raw.get("opening") or {}
    opening_name = opening.get("name")
    clock = raw.get("clock") or {}
    division = raw.get("division") or {}
    analysis = raw.get("analysis") or []
    sans = (raw.get("moves") or "").split()
    clocks = raw.get("clocks") or []

    game = {
        "id": raw["id"],
        "rated": raw.get("rated"),
        "variant": raw.get("variant"),
        "speed": raw.get("speed"),
        "perf": raw.get("perf"),
        "created_at": _ts(raw.get("createdAt")),
        "last_move_at": _ts(raw.get("lastMoveAt")),
        "status": raw.get("status"),
        "winner": raw.get("winner"),
        "white": w["id"],
        "black": b["id"],
        "white_name": w["name"],
        "black_name": b["name"],
        "white_rating": w["rating"],
        "black_rating": b["rating"],
        "white_title": w["title"],
        "black_title": b["title"],
        "eco": opening.get("eco"),
        "opening_name": opening_name,
        "opening_family": opening_name.split(":")[0].strip() if opening_name else None,
        "opening_ply": opening.get("ply"),
        "clock_initial": clock.get("initial"),
        "clock_increment": clock.get("increment"),
        "div_middle": division.get("middle"),
        "div_end": division.get("end"),
        "num_plies": len(sans),
        "has_analysis": bool(analysis),
        "white_accuracy": w["accuracy"],
        "black_accuracy": b["accuracy"],
        "source": raw.get("source"),
    }
    parsed = ParsedGame(game=game)

    if raw.get("variant", "standard") != "standard":
        # Variantes con otra posición inicial no aportan al repertorio estándar.
        return parsed

    board = chess.Board(raw["initialFen"]) if raw.get("initialFen") else chess.Board()
    initial_cs = clock["initial"] * 100 if "initial" in clock else None
    inc_cs = clock.get("increment", 0) * 100

    for i, san in enumerate(sans):
        try:
            move = board.parse_san(san)
        except ValueError:
            log.warning("Jugada ilegal %r en %s (ply %d); se corta el parseo", san, raw["id"], i + 1)
            break
        before = epd(board)
        color = "white" if board.turn == chess.WHITE else "black"
        piece = board.piece_at(move.from_square)
        is_capture = board.is_capture(move)
        board.push(move)
        after = epd(board)

        # clocks[i] = reloj de quien movió, tras el ply i+1.
        clock_cs = clocks[i] if i < len(clocks) else None
        spent_cs = None
        if clock_cs is not None:
            prev = clocks[i - 2] if i >= 2 else initial_cs
            if prev is not None:
                spent_cs = max(0, prev - clock_cs + inc_cs)

        parsed.moves.append({
            "game_id": raw["id"],
            "ply": i + 1,
            "color": color,
            "san": san,
            "uci": move.uci(),
            "epd_before": before,
            "epd_after": after,
            "clock_cs": clock_cs,
            "spent_cs": spent_cs,
            "is_capture": is_capture,
            "is_check": board.is_check(),
            "piece": piece.symbol().lower() if piece else None,
        })

        # analysis[i] = evaluación tras el ply i+1 (desde las blancas).
        if i < len(analysis):
            a = analysis[i]
            if "eval" in a or "mate" in a:
                parsed.evals.append({
                    "epd": after,
                    "cp": a.get("eval"),
                    "mate": a.get("mate"),
                    "best_uci": None,
                    "pv": None,
                    "depth": None,
                    "source": "lichess",
                })

    return parsed


# --- sincronización ------------------------------------------------------


def _sync_key(username: str, cfg: Config) -> str:
    perfs = ",".join(sorted(cfg.download.perf_types))
    return f"{username}|{perfs}|{'rated' if cfg.download.rated_only else 'all'}"


def store_games(con: duckdb.DuckDBPyConnection, parsed: list[ParsedGame], raw_file: Path | None = None,
                raws: list[dict[str, Any]] | None = None) -> None:
    if not parsed:
        return
    con.execute("BEGIN")
    try:
        db.bulk_insert(con, "games", [p.game for p in parsed])
        db.bulk_insert(con, "moves", [m for p in parsed for m in p.moves])
        db.bulk_insert(con, "evals", [e for p in parsed for e in p.evals])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    if raw_file is not None and raws:
        raw_file.parent.mkdir(parents=True, exist_ok=True)
        with open(raw_file, "a", encoding="utf-8") as f:
            for r in raws:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _download_range(
    client: LichessClient,
    con: duckdb.DuckDBPyConnection,
    cfg: Config,
    username: str,
    since: int | None,
    until: int | None,
    max_games: int | None,
    on_game: Callable[[], None],
    ascending: bool = False,
) -> tuple[int, int | None, int | None]:
    """Descarga un rango (más recientes primero). Devuelve (n, ts_más_nuevo, ts_más_viejo)."""
    count, newest, oldest = 0, None, None
    batch: list[ParsedGame] = []
    raws: list[dict[str, Any]] = []
    raw_file = cfg.data_path / "raw" / f"{username}.ndjson"

    def flush() -> None:
        store_games(con, batch, raw_file, raws)
        batch.clear()
        raws.clear()

    try:
        for raw in client.iter_user_games(
            username,
            since=since,
            until=until,
            max_games=max_games,
            perf_types=cfg.download.perf_types,
            rated=True if cfg.download.rated_only else None,
            ascending=ascending,
        ):
            created = raw.get("createdAt")
            newest = created if newest is None else max(newest, created)
            oldest = created if oldest is None else min(oldest, created)
            batch.append(parse_game(raw))
            raws.append(raw)
            count += 1
            on_game()
            if len(batch) >= BATCH_SIZE:
                flush()
    finally:
        flush()
    return count, newest, oldest


def sync_user(
    client: LichessClient,
    con: duckdb.DuckDBPyConnection,
    cfg: Config,
    username: str,
    on_game: Callable[[], None] = lambda: None,
) -> int:
    """Descarga incremental: primero lo nuevo, después completa hacia atrás hasta `since`/`max_games`."""
    username = username.lower()
    key = _sync_key(username, cfg)
    dl = cfg.download
    since_ms = to_millis(dl.since)
    until_ms = to_millis(dl.until, end_of_day=True)

    row = con.execute(
        "SELECT newest_ms, oldest_ms, backfill_since_ms, backfill_done FROM sync_state WHERE key = ?", [key]
    ).fetchone()
    newest_ms, oldest_ms, backfill_since_ms, backfill_done = row if row else (None, None, None, False)
    total = 0

    def save_state() -> None:
        con.execute(
            "INSERT OR REPLACE INTO sync_state VALUES (?, ?, ?, ?, ?, ?, current_timestamp)",
            [key, username, newest_ms, oldest_ms, backfill_since_ms, backfill_done],
        )

    for _ in range(5):  # reanudaciones ante cortes del stream
        try:
            # 1) Partidas nuevas desde la última descarga, en orden ascendente: si el stream
            #    se corta, lo guardado es un tramo continuo y newest_ms sigue siendo válido.
            if newest_ms is not None and (until_ms is None or newest_ms < until_ms):
                n, newest, _oldest = _download_range(
                    client, con, cfg, username, newest_ms + 1, until_ms, None, on_game, ascending=True
                )
                total += n
                if newest is not None:
                    newest_ms = newest
                save_state()

            # 2) Hacia atrás: primera descarga o completar hasta `since` / `max_games`.
            stored = _count_user_games(con, cfg, username)
            needs_backfill = (
                newest_ms is None
                or not backfill_done
                or (since_ms is not None and (backfill_since_ms is None or since_ms < backfill_since_ms))
            )
            remaining = None if dl.max_games is None else dl.max_games - stored
            if needs_backfill and (remaining is None or remaining > 0):
                upper = until_ms if oldest_ms is None else oldest_ms - 1
                n, newest, oldest = _download_range(
                    client, con, cfg, username, since_ms, upper, remaining, on_game
                )
                total += n
                if newest is not None and (newest_ms is None or newest > newest_ms):
                    newest_ms = newest
                if oldest is not None:
                    oldest_ms = oldest if oldest_ms is None else min(oldest_ms, oldest)
                # Si vinieron menos de las pedidas, llegamos al principio del rango.
                backfill_done = remaining is None or n < remaining
                backfill_since_ms = since_ms if backfill_done else backfill_since_ms
                save_state()
            return total
        except StreamInterrupted as e:
            log.warning("%s. Reanudando %s...", e, username)
            # Recalcular el estado desde la base para no perder lo ya guardado.
            r = con.execute(
                "SELECT max(epoch_ms(created_at)), min(epoch_ms(created_at)) FROM player_games "
                "WHERE player = ?",
                [username],
            ).fetchone()
            if r and r[0] is not None:
                newest_ms, oldest_ms = r
            save_state()
    return total


def _count_user_games(con: duckdb.DuckDBPyConnection, cfg: Config, username: str) -> int:
    perfs = cfg.download.perf_types
    placeholders = ",".join("?" for _ in perfs)
    sql = f"SELECT count(*) FROM player_games WHERE player = ? AND perf IN ({placeholders})"
    params: list[Any] = [username, *perfs]
    if cfg.download.rated_only:
        sql += " AND rated"
    return con.execute(sql, params).fetchone()[0]
