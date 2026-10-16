"""Arma el informe de un rival como JSON (consumido por web/ y reutilizable en la etapa 2)."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import chess
import duckdb

from tegchess import __version__
from tegchess.analysis.openings import opening_summary, repeated_positions, repertoire_tree
from tegchess.analysis.scope import scope_cte
from tegchess.config import PROJECT_ROOT, Config
from tegchess.db import fetch_dicts
from tegchess.engine import epd_to_fen

WEB_DIR = PROJECT_ROOT / "web"
COLORS = ("white", "black")


def profile(con: duckdb.DuckDBPyConnection, cfg: Config, player: str) -> dict[str, Any]:
    cte, params = scope_cte(cfg, player)
    summary = fetch_dicts(con, f"""
        WITH {cte}
        SELECT count(*) AS games,
               round(100 * avg(score), 1) AS score_pct,
               round(avg(rating)) AS avg_rating,
               arg_max(rating, created_at) AS last_rating,
               min(created_at) AS first_game, max(created_at) AS last_game,
               round(avg(num_plies) / 2, 1) AS avg_moves,
               round(avg(accuracy), 1) AS avg_accuracy
        FROM scope
    """, params)[0]
    by_color = fetch_dicts(con, f"""
        WITH {cte}
        SELECT color, count(*) AS games,
               count(*) FILTER (WHERE score = 1) AS wins,
               count(*) FILTER (WHERE score = 0.5) AS draws,
               count(*) FILTER (WHERE score = 0) AS losses,
               round(100 * avg(score), 1) AS score_pct
        FROM scope GROUP BY color ORDER BY color DESC
    """, params)
    by_perf = fetch_dicts(con, f"""
        WITH {cte}
        SELECT perf, count(*) AS games, round(100 * avg(score), 1) AS score_pct, round(avg(rating)) AS avg_rating
        FROM scope GROUP BY perf ORDER BY games DESC
    """, params)
    # Cómo termina las partidas que pierde y las que gana (caídas por tiempo, abandonos, mates...).
    endings = fetch_dicts(con, f"""
        WITH {cte}
        SELECT status,
               count(*) FILTER (WHERE score = 0) AS losses,
               count(*) FILTER (WHERE score = 1) AS wins,
               count(*) FILTER (WHERE score = 0.5) AS draws
        FROM scope GROUP BY status ORDER BY losses + wins + draws DESC
    """, params)
    return {"summary": summary, "by_color": by_color, "by_perf": by_perf, "endings": endings}


def _line_to(con: duckdb.DuckDBPyConnection, game_id: str, ply: int) -> list[dict[str, str]]:
    """Jugadas hasta la posición (sin incluir el ply), con el FEN tras cada una para el tablero."""
    rows = con.execute(
        "SELECT san, uci FROM moves WHERE game_id = ? AND ply < ? ORDER BY ply", [game_id, ply]
    ).fetchall()
    board = chess.Board()
    line = []
    for san, uci in rows:
        board.push_uci(uci)
        line.append({"san": san, "uci": uci, "fen": board.fen()})
    return line


def _san(epd: str, uci: str | None) -> str | None:
    if not uci:
        return None
    board = chess.Board(epd_to_fen(epd))
    try:
        return board.san(chess.Move.from_uci(uci))
    except (ValueError, AssertionError):
        return None


def weak_positions(con: duckdb.DuckDBPyConnection, cfg: Config, player: str, limit: int = 25) -> list[dict[str, Any]]:
    """Posiciones repetidas del jugador con su jugada habitual vs. la del motor, y la línea para llegar."""
    out = []
    for r in repeated_positions(con, cfg, player, limit=limit):
        if not r["avg_wp_loss"]:
            continue
        sample = r["sample_games"][0]
        ply = con.execute(
            "SELECT ply FROM moves WHERE game_id = ? AND epd_before = ? ORDER BY ply LIMIT 1", [sample, r["epd"]]
        ).fetchone()[0]
        out.append({
            **r,
            "fen": epd_to_fen(r["epd"]),
            "best_san": _san(r["epd"], r["best_uci"]),
            "line": _line_to(con, sample, ply),
            "eval_for_player": None if r["eval_cp"] is None
            else (r["eval_cp"] if r["color"] == "white" else -r["eval_cp"]),
        })
    return out


def player_section(con: duckdb.DuckDBPyConnection, cfg: Config, player: str) -> dict[str, Any]:
    return {
        "username": player,
        "profile": profile(con, cfg, player),
        "openings": {c: opening_summary(con, cfg, player, c) for c in COLORS},
        "openings_detail": {c: opening_summary(con, cfg, player, c, by="name") for c in COLORS},
        "trees": {c: repertoire_tree(con, cfg, player, c) for c in COLORS},
        "weak_positions": weak_positions(con, cfg, player),
    }


def build_report(con: duckdb.DuckDBPyConnection, cfg: Config, rival: str) -> dict[str, Any]:
    rival = rival.lower()
    me = cfg.me.lower() if cfg.me else None
    eval_coverage = con.execute(f"""
        SELECT count(*) FILTER (WHERE e.epd IS NOT NULL), count(*)
        FROM moves m LEFT JOIN evals e ON e.epd = m.epd_after
        JOIN games g ON g.id = m.game_id
        WHERE (g.white = ? OR g.black = ?) AND (m.ply <= {int(cfg.engine.max_ply)} OR {int(cfg.engine.max_ply)} = 0)
    """, [rival, rival]).fetchone()
    return {
        "meta": {
            "version": __version__,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "me": me,
            "rival": rival,
            "perf_types": cfg.download.perf_types,
            "since": str(cfg.download.since) if cfg.download.since else None,
            "until": str(cfg.download.until) if cfg.download.until else None,
            "max_games": cfg.download.max_games,
            "engine_depth": cfg.engine.depth,
            "analysed_plies": cfg.engine.max_ply,
            "eval_coverage_pct": round(100 * eval_coverage[0] / eval_coverage[1], 1) if eval_coverage[1] else 0,
        },
        "rival": player_section(con, cfg, rival),
        "me": player_section(con, cfg, me) if me and me != rival else None,
    }


def _json_default(o: Any) -> Any:
    if isinstance(o, datetime):
        return o.isoformat()
    return str(o)


def write_report(report: dict[str, Any], reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{report['meta']['rival']}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, default=_json_default), encoding="utf-8")
    return path


def export_static(reports: list[dict[str, Any]], out_dir: Path) -> Path:
    """Copia la interfaz web con los informes incrustados: se abre con doble clic, sin servidor."""
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(WEB_DIR, out_dir)
    payload = {r["meta"]["rival"]: r for r in reports}
    data_js = "window.TEGCHESS_STATIC = " + json.dumps(payload, ensure_ascii=False, default=_json_default) + ";\n"
    (out_dir / "static-data.js").write_text(data_js, encoding="utf-8")
    return out_dir / "index.html"
