"""Alcance de las consultas: qué partidas de un jugador entran en el análisis."""

from __future__ import annotations

from typing import Any

from tegchess.config import Config, normalize_player


def scope_cte(cfg: Config, player: str, color: str | None = None, name: str = "scope") -> tuple[str, list[Any]]:
    """CTE `scope` (o `name`) con las partidas del jugador según el config (origen, modalidad, fechas, max_games).

    Columnas: las de player_games (game_id, color, player, opponent, score, ...).
    Con `name` distinto se pueden combinar los alcances de dos jugadores en una consulta.
    """
    dl = cfg.download
    where = ["player = ?", "variant = 'standard'", "coalesce(origin, 'lichess') = ?"]
    params: list[Any] = [normalize_player(player), "pgn" if cfg.is_pgn else "lichess"]
    # Modalidad y rated son filtros de la descarga de Lichess; en modo PGN entra todo lo de la carpeta.
    if dl.perf_types and not cfg.is_pgn:
        where.append(f"perf IN ({','.join('?' for _ in dl.perf_types)})")
        params += dl.perf_types
    if dl.rated_only and not cfg.is_pgn:
        where.append("rated")
    # Fechas y tope de partidas también son de la descarga: en modo PGN manda la carpeta.
    if dl.since and not cfg.is_pgn:
        where.append("created_at >= ?")
        params.append(str(dl.since))
    if dl.until and not cfg.is_pgn:
        where.append("created_at < CAST(? AS DATE) + INTERVAL 1 DAY")
        params.append(str(dl.until))
    limit = f"LIMIT {int(dl.max_games)}" if dl.max_games and not cfg.is_pgn else ""
    color_filter = ""
    if color:
        color_filter = "WHERE color = ?"
        params.append(color)
    sql = f"""
    {name}_all AS (
        SELECT * FROM player_games
        WHERE {' AND '.join(where)}
        ORDER BY created_at DESC
        {limit}
    ),
    {name} AS (SELECT * FROM {name}_all {color_filter})
    """
    return sql, params
