"""Alcance de las consultas: qué partidas de un jugador entran en el análisis."""

from __future__ import annotations

from typing import Any

from tegchess.config import Config


def scope_cte(cfg: Config, player: str, color: str | None = None) -> tuple[str, list[Any]]:
    """CTE `scope` con las partidas del jugador según el config (modalidad, fechas, max_games).

    Columnas: las de player_games (game_id, color, player, opponent, score, ...).
    """
    dl = cfg.download
    where = ["player = ?", "variant = 'standard'"]
    params: list[Any] = [player.lower()]
    if dl.perf_types:
        where.append(f"perf IN ({','.join('?' for _ in dl.perf_types)})")
        params += dl.perf_types
    if dl.rated_only:
        where.append("rated")
    if dl.since:
        where.append("created_at >= ?")
        params.append(str(dl.since))
    if dl.until:
        where.append("created_at < CAST(? AS DATE) + INTERVAL 1 DAY")
        params.append(str(dl.until))
    limit = f"LIMIT {int(dl.max_games)}" if dl.max_games else ""
    color_filter = ""
    if color:
        color_filter = "WHERE color = ?"
        params.append(color)
    sql = f"""
    scope_all AS (
        SELECT * FROM player_games
        WHERE {' AND '.join(where)}
        ORDER BY created_at DESC
        {limit}
    ),
    scope AS (SELECT * FROM scope_all {color_filter})
    """
    return sql, params
