"""Pasos del flujo (descargar, analizar, generar informes) usados por la CLI y el servidor."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any
from pathlib import Path

from tegchess import db
from tegchess.config import Config, lichess_token, normalize_player
from tegchess.engine import evaluate_pending
from tegchess.ingest import sync_user
from tegchess.lichess import LichessClient, LichessError
from tegchess.report import build_me_report, build_report, export_static, write_report

log = logging.getLogger(__name__)

# progress(descripción, total o None) -> función a llamar por cada unidad procesada
Progress = Callable[[str, int | None], Callable[[], None]]


def _noop_progress(_desc: str, _total: int | None) -> Callable[[], None]:
    return lambda: None


def fetch_all(cfg: Config, users: list[str] | None = None, progress: Progress = _noop_progress) -> dict[str, Any]:
    """Descarga incremental de cada usuario (Lichess) o importación de la carpeta de PGN (modo PGN).

    Devuelve {usuario: nuevas | mensaje de error} o, en modo PGN, el resumen de la importación.
    """
    cfg = cfg.active()
    if cfg.is_pgn:
        from tegchess.pgn_import import import_folder

        con = db.connect(cfg.db_path)
        try:
            return {"pgn": import_folder(con, cfg, progress)}
        finally:
            con.close()
    users = users or cfg.players()
    result: dict[str, int | str] = {}
    con = db.connect(cfg.db_path)
    try:
        with LichessClient(lichess_token()) as client:
            for user in users:
                tick = progress(f"Descargando {user}", cfg.download.max_games)
                try:
                    result[user] = sync_user(client, con, cfg, user, on_game=tick)
                except LichessError as e:
                    log.error("%s: %s", user, e)
                    result[user] = str(e)
    finally:
        con.close()
    return result


def analyze(cfg: Config, progress: Progress = _noop_progress) -> dict[str, int]:
    cfg = cfg.active()
    con = db.connect(cfg.db_path)
    try:
        with LichessClient(lichess_token()) as client:
            return evaluate_pending(con, cfg, client, progress)
    finally:
        con.close()


def build_reports(cfg: Config, rivals: list[str] | None = None, progress: Progress = _noop_progress) -> list[Path]:
    cfg = cfg.active()
    rivals = [normalize_player(r) for r in (rivals or cfg.opponents)]
    tick = progress("Generando informes", len(rivals) + (1 if cfg.me else 0))
    paths = []
    cache: dict = {}   # mi sección es la misma en todos los informes
    con = db.connect(cfg.db_path)
    try:
        for rival in rivals:
            paths.append(write_report(build_report(con, cfg, rival, cache), cfg.reports_path))
            tick()
        if cfg.me:
            paths.append(write_report(build_me_report(con, cfg, cache), cfg.reports_path))
            tick()
    finally:
        con.close()
    return paths


def export_html(cfg: Config) -> Path:
    """Exporta todos los informes generados a data/export (HTML estático)."""
    import json

    reports = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(cfg.reports_path.glob("*.json"))]
    return export_static(reports, cfg.data_path / ("export-pgn" if cfg.is_pgn else "export"))
