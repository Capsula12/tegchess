"""Manejo del tiempo: caídas por reloj, tiempo por fase, apuros y calidad de juego con poco reloj."""

from __future__ import annotations

from typing import Any

import duckdb

from tegchess.analysis.expectation import MIN_PROFILE_GAMES
from tegchess.analysis.scope import scope_cte
from tegchess.config import Config
from tegchess.db import fetch_dicts

# Apuro: le queda menos de esta fracción del tiempo inicial.
TROUBLE_FRACTION = 0.10
# Pensada larga: una jugada que consume al menos esta fracción del tiempo inicial.
LONG_THINK_FRACTION = 0.10
# Jugadas propias en las que se mide cuánto reloj le queda.
CLOCK_CHECKPOINTS = (10, 20, 30, 40)
# Las dos primeras jugadas no cuentan para el tiempo gastado (en Lichess no corre el reloj).
FIRST_TIMED_PLY = 3


def _moves_cte(cte: str) -> str:
    """CTEs `g` (partidas con reloj) y `mv` (jugadas propias con fase y fracción de reloj)."""
    return f"""
    WITH {cte},
    g AS (
        SELECT game_id, color, score, status, perf, num_plies, div_middle, div_end,
               clock_initial * 100 AS init_cs, clock_increment * 100 AS inc_cs
        FROM scope
        WHERE clock_initial > 0
    ),
    mv AS (
        SELECT g.*, m.ply, m.clock_cs, m.spent_cs, (m.ply + 1) // 2 AS move_no,
               CASE
                   WHEN g.div_middle IS NULL OR m.ply < g.div_middle THEN 'opening'
                   WHEN g.div_end IS NULL OR m.ply < g.div_end THEN 'middlegame'
                   ELSE 'endgame'
               END AS phase,
               m.clock_cs / g.init_cs AS clock_frac
        FROM g JOIN moves m ON m.game_id = g.game_id AND m.color = g.color
        WHERE m.clock_cs IS NOT NULL
    )
    """


def time_profile(con: duckdb.DuckDBPyConnection, cfg: Config, player: str) -> dict[str, Any]:
    cte, params = scope_cte(cfg, player)
    base = _moves_cte(cte)

    summary = fetch_dicts(con, f"""
        {base},
        per_game AS (
            SELECT game_id, any_value(score) AS score,
                   min(move_no) FILTER (WHERE clock_frac < {TROUBLE_FRACTION}) AS trouble_move,
                   max(spent_cs / init_cs) FILTER (WHERE ply >= {FIRST_TIMED_PLY}) AS longest_frac,
                   arg_max(move_no, spent_cs) FILTER (WHERE ply >= {FIRST_TIMED_PLY}) AS longest_move,
                   count(*) FILTER (WHERE ply >= {FIRST_TIMED_PLY}
                                    AND spent_cs >= {LONG_THINK_FRACTION} * init_cs) AS long_thinks
            FROM mv GROUP BY game_id
        )
        SELECT (SELECT count(*) FROM g) AS games,
               (SELECT count(*) FROM g WHERE score = 0) AS losses,
               (SELECT count(*) FROM g WHERE status = 'outoftime' AND score = 0) AS lost_on_time,
               (SELECT count(*) FROM g WHERE status = 'outoftime' AND score = 1) AS won_on_time,
               count(*) FILTER (WHERE trouble_move IS NOT NULL) AS trouble_games,
               round(avg(trouble_move), 1) AS avg_trouble_move,
               round(100 * avg(score) FILTER (WHERE trouble_move IS NOT NULL), 1) AS score_in_trouble_pct,
               round(100 * avg(score) FILTER (WHERE trouble_move IS NULL), 1) AS score_no_trouble_pct,
               round(100 * avg(longest_frac), 1) AS avg_longest_think_pct,
               round(avg(longest_move), 1) AS avg_longest_think_move,
               round(avg(long_thinks), 2) AS long_thinks_per_game
        FROM per_game
    """, params)[0]
    games = summary["games"] or 0
    summary["lost_on_time_pct"] = round(100 * summary["lost_on_time"] / games, 1) if games else None
    summary["lost_on_time_of_losses_pct"] = (
        round(100 * summary["lost_on_time"] / summary["losses"], 1) if summary["losses"] else None)
    summary["trouble_pct"] = round(100 * summary["trouble_games"] / games, 1) if games else None

    # Segundos por jugada y reparto del tiempo según la fase, por modalidad (3+0 y 10+5 no se mezclan bien).
    by_phase = fetch_dicts(con, f"""
        {base}
        SELECT perf, phase, count(*) AS moves,
               round(avg(spent_cs) / 100, 1) AS avg_seconds,
               round(100 * sum(spent_cs) / sum(sum(spent_cs)) OVER (PARTITION BY perf), 1) AS share_pct
        FROM mv WHERE ply >= {FIRST_TIMED_PLY}
        GROUP BY perf, phase
        ORDER BY perf, CASE phase WHEN 'opening' THEN 1 WHEN 'middlegame' THEN 2 ELSE 3 END
    """, params)

    checkpoints = ", ".join(str(n) for n in CLOCK_CHECKPOINTS)
    clock_curve = fetch_dicts(con, f"""
        {base}
        SELECT move_no, count(*) AS games, round(100 * avg(clock_frac), 1) AS clock_left_pct
        FROM mv WHERE move_no IN ({checkpoints})
        GROUP BY move_no ORDER BY move_no
    """, params)

    # Calidad con poco reloj: solo jugadas con evaluación (las primeras N, o partidas analizadas en Lichess).
    pressure = fetch_dicts(con, f"""
        {base}
        SELECT CASE WHEN mv.clock_frac < {TROUBLE_FRACTION} THEN 'low' ELSE 'normal' END AS clock,
               count(*) AS moves,
               round(avg(q.wp_loss), 2) AS avg_wp_loss,
               round(100.0 * count(*) FILTER (WHERE q.judgment IN ('mistake', 'blunder')) / count(*), 1)
                   AS error_rate_pct,
               round(100.0 * count(*) FILTER (WHERE q.judgment = 'blunder') / count(*), 1) AS blunder_rate_pct
        FROM mv JOIN move_quality q ON q.game_id = mv.game_id AND q.ply = mv.ply
        WHERE q.judgment IS NOT NULL AND mv.ply >= {FIRST_TIMED_PLY}
        GROUP BY 1
    """, params)

    profile = {
        "summary": summary,
        "by_phase": by_phase,
        "clock_curve": clock_curve,
        "pressure": {p["clock"]: p for p in pressure},
        "thresholds": {"trouble_pct": round(100 * TROUBLE_FRACTION), "long_think_pct": round(100 * LONG_THINK_FRACTION)},
    }
    profile["hints"] = time_hints(profile)
    return profile


def time_hints(tp: dict[str, Any]) -> list[str]:
    """Ideas para la partida a partir del perfil de tiempo (frases cortas, en segunda persona)."""
    s = tp["summary"]
    hints: list[str] = []
    if not s["games"] or s["games"] < MIN_PROFILE_GAMES:
        return hints
    trouble = tp["thresholds"]["trouble_pct"]
    if s["trouble_pct"] is not None and s["trouble_pct"] >= 30:
        text = (f"Se apura seguido: en el {s['trouble_pct']:.0f}% de sus partidas baja del {trouble}% del reloj "
                f"(en promedio en la jugada {s['avg_trouble_move']:.0f}).")
        low, normal = tp["pressure"].get("low"), tp["pressure"].get("normal")
        if low and normal and low["moves"] >= 30 and low["error_rate_pct"] > normal["error_rate_pct"]:
            text += (f" Con poco tiempo comete errores en el {low['error_rate_pct']:.0f}% de las jugadas "
                     f"(con tiempo, {normal['error_rate_pct']:.0f}%).")
        hints.append(text + " Buscá posiciones complejas que lo obliguen a pensar y no te apures a simplificar.")
    if s["lost_on_time_of_losses_pct"] is not None and s["losses"] >= 5 and s["lost_on_time_of_losses_pct"] >= 25:
        hints.append(f"El {s['lost_on_time_of_losses_pct']:.0f}% de sus derrotas son por tiempo "
                     f"({s['lost_on_time']} de {s['losses']}): si la posición está pareja, jugá rápido y seguro.")
    if (s["score_in_trouble_pct"] is not None and s["score_no_trouble_pct"] is not None and s["trouble_games"] >= 5
            and s["score_no_trouble_pct"] - s["score_in_trouble_pct"] >= 15):
        hints.append(f"Cuando se apura hace {s['score_in_trouble_pct']:.0f}% de los puntos; sin apuro, "
                     f"{s['score_no_trouble_pct']:.0f}%.")
    if s["avg_longest_think_pct"] is not None and s["avg_longest_think_pct"] >= 15:
        hints.append(f"Hace pensadas largas: su jugada más lenta se lleva en promedio el {s['avg_longest_think_pct']:.0f}% "
                     f"del tiempo inicial (alrededor de la jugada {s['avg_longest_think_move']:.0f}).")
    return hints
