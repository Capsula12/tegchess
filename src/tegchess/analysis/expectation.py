"""Expectativa de resultado: Elo como base y ajustes por posición, con la incertidumbre a la vista.

Calibrado con un backtest sobre partidas reales (docs/PROPUESTA_ANALISIS.md):
- El Elo es el mejor predictor por sí solo.
- La ventaja según el motor se traduce en ~60% de su % de victoria (ajuste lineal estable entre los
  plies 8 y 30): ENGINE_WEIGHT.
- El resultado por apertura o posición, aun corregido por Elo, es muy ruidoso: se suaviza con
  RESIDUAL_K partidas ficticias "como el Elo esperaba".
"""

from __future__ import annotations

import math
from typing import Any

import duckdb

from tegchess.analysis.scope import scope_cte
from tegchess.config import Config
from tegchess.db import fetch_dicts
from tegchess.metrics import win_pct

ENGINE_WEIGHT = 0.6
RESIDUAL_K = 30
LOW_SAMPLE = 10          # menos partidas que esto: "poca muestra"
RECENT_GAMES = 30


def elo_expected(rating: float | None, opp_rating: float | None) -> float | None:
    """Puntaje esperado (0-1) del primero según la fórmula de Elo."""
    if rating is None or opp_rating is None:
        return None
    return 1 / (1 + 10 ** ((opp_rating - rating) / 400))


def shrunk_residual(residual_sum: float | None, n: int, k: int = RESIDUAL_K) -> float:
    """Desvío medio sobre lo esperado por Elo, encogido hacia 0 con k partidas ficticias."""
    if not n or residual_sum is None:
        return 0.0
    return residual_sum / (n + k)


def wilson(score: float | None, n: int, z: float = 1.64) -> tuple[float, float] | None:
    """Intervalo de Wilson (90%) para un puntaje 0-1 con n partidas, en %."""
    if not n or score is None:
        return None
    denom = 1 + z * z / n
    center = (score + z * z / (2 * n)) / denom
    half = z * math.sqrt(score * (1 - score) / n + z * z / (4 * n * n)) / denom
    return round(100 * max(0.0, center - half), 1), round(100 * min(1.0, center + half), 1)


def confidence(n: int) -> str:
    return "baja" if n < LOW_SAMPLE else "media" if n < 30 else "alta"


def expected_with_adjustments(base: float, eval_for_me: float | None = None,
                              my_residual: tuple[float | None, int] = (None, 0),
                              rival_residual: tuple[float | None, int] = (None, 0)) -> dict[str, Any]:
    """Esperado (0-100) = base por Elo + motor + desvíos (mío a favor, el suyo en contra).

    Los residuos vienen como (suma de (resultado − esperado por Elo), partidas); el del rival es desde él.
    Devuelve el esperado y el aporte de cada parte, en puntos de %.
    """
    engine = 0.0 if eval_for_me is None else ENGINE_WEIGHT * (win_pct(eval_for_me) - 50)
    mine = 100 * shrunk_residual(*my_residual)
    theirs = -100 * shrunk_residual(*rival_residual)
    total = min(99.0, max(1.0, 100 * base + engine + mine + theirs))
    return {"expected_pct": round(total, 1), "base_pct": round(100 * base, 1),
            "delta_engine": round(engine, 1), "delta_me": round(mine, 1), "delta_rival": round(theirs, 1),
            "delta_pct": round(total - 100 * base, 1)}


def strength(con: duckdb.DuckDBPyConnection, cfg: Config, player: str) -> dict[str, Any]:
    """Rating actual, rendimiento sobre lo esperado por Elo (general, por color y modalidad) y forma reciente."""
    cte, params = scope_cte(cfg, player)
    base = f"""
        WITH {cte},
        g AS (
            SELECT *, 1 / (1 + pow(10, (opp_rating - rating) / 400.0)) AS exp_score,
                   row_number() OVER (ORDER BY created_at DESC) AS recency
            FROM scope WHERE rating IS NOT NULL AND opp_rating IS NOT NULL
        )
    """

    def perf_rows(group: str) -> list[dict[str, Any]]:
        return fetch_dicts(con, f"""
            {base}
            SELECT {group} AS key, count(*) AS games,
                   round(100 * avg(score), 1) AS score_pct,
                   round(100 * avg(exp_score), 1) AS expected_pct,
                   round(100 * (avg(score) - avg(exp_score)), 1) AS over_pct,
                   round(avg(opp_rating)) AS avg_opp_rating
            FROM g GROUP BY 1 ORDER BY games DESC
        """, params)

    overall = perf_rows("'all'")
    by_color = perf_rows("color")
    by_perf = perf_rows("perf")
    form = perf_rows(f"CASE WHEN recency <= {RECENT_GAMES} THEN 'recent' ELSE 'before' END")
    ratings = fetch_dicts(con, f"""
        {base}
        SELECT perf, arg_max(rating, created_at) AS current, count(*) AS games,
               arg_max(rating, created_at) - arg_min(rating, created_at) AS change,
               max(created_at) AS last_game
        FROM g GROUP BY perf ORDER BY games DESC
    """, params)
    return {
        "overall": overall[0] if overall else None,
        "by_color": by_color,
        "by_perf": by_perf,
        "form": {r["key"]: r for r in form},
        "ratings": ratings,
    }


def matchup_base(me: dict[str, Any], rival: dict[str, Any]) -> dict[str, Any] | None:
    """Expectativa base yo vs. rival: rating actual en la modalidad que más juegan los dos."""
    mine = {r["perf"]: r for r in me["ratings"]}
    theirs = {r["perf"]: r for r in rival["ratings"]}
    common = sorted(mine.keys() & theirs.keys(), key=lambda p: -min(mine[p]["games"], theirs[p]["games"]))
    if not common:
        return None
    perf = common[0]
    my_r, rv_r = mine[perf]["current"], theirs[perf]["current"]
    exp = elo_expected(my_r, rv_r)
    gap = rv_r - my_r
    return {
        "perf": perf, "my_rating": my_r, "rival_rating": rv_r, "gap": gap,
        "expected_pct": round(100 * exp, 1),
        # Desvío sobre Elo de cada uno en general (suavizado): rinde más o menos que su rating.
        "my_over_pct": me["overall"]["over_pct"] if me["overall"] else None,
        "rival_over_pct": rival["overall"]["over_pct"] if rival["overall"] else None,
        "big_gap": abs(gap) >= 200,
    }
