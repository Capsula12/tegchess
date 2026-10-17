"""Mi juego: qué mejorar en general, ordenado por cuántos puntos cuesta cada problema.

Cada área estima su costo en **puntos cada 100 partidas** (1 punto = una victoria; tablas = medio),
con supuestos simples y explícitos para poder compararlas entre sí:
- Reloj: derrotas por tiempo, valuadas por la última evaluación conocida (si no hay, medio punto).
- Aperturas: familias donde salgo peor según el motor (la señal estable), con el peso práctico del motor.
- Conversión y defensa: partidas con +3 que no gano / con −3 que no salvo.
- Errores: por fase y por tipo (táctico o posicional), oportunidades que dejo pasar.
- Tendencias: rendimiento sobre el Elo después de perder y en sesiones largas, por color y modalidad.
- Finales: resultado por tipo de final.

Las métricas que dependen del motor usan solo jugadas evaluadas; con engine.analyse_my_games_fully
están todas mis partidas completas.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import chess
import duckdb

from tegchess.analysis.clock import time_profile
from tegchess.analysis.expectation import ENGINE_WEIGHT, confidence
from tegchess.analysis.openings import opening_summary
from tegchess.analysis.scope import scope_cte
from tegchess.config import Config
from tegchess.db import fetch_dicts
from tegchess.metrics import win_pct

WINNING_CP = 300          # "posición ganada" para conversión / defensa
MIN_FAMILY_GAMES = 8
SESSION_GAP_MIN = 45      # minutos entre partidas para considerar que es la misma sesión
PHASES = {"opening": "Apertura", "middlegame": "Medio juego", "endgame": "Final"}


def _per100(points: float, games: int) -> float:
    return round(100 * points / games, 1) if games else 0.0


def _evaluated_moves_cte(cte: str) -> str:
    """Mis jugadas con evaluación, fase y la jugada anterior del rival."""
    return f"""
    WITH {cte},
    q AS (
        SELECT mq.*, s.score, s.status, s.div_middle, s.div_end, s.opp_rating, s.rating,
               CASE
                   WHEN s.div_middle IS NULL OR mq.ply < s.div_middle THEN 'opening'
                   WHEN s.div_end IS NULL OR mq.ply < s.div_end THEN 'middlegame'
                   ELSE 'endgame'
               END AS phase,
               CASE s.color WHEN 'white' THEN 1 ELSE -1 END * mq.before_cp AS before_me,
               lag(mq.wp_loss) OVER (PARTITION BY mq.game_id ORDER BY mq.ply) AS prev_wp_loss,
               mq.color = s.color AS mine
        FROM move_quality mq JOIN scope s ON s.game_id = mq.game_id
    )
    """


def time_area(tp: dict[str, Any], con: duckdb.DuckDBPyConnection, cfg: Config, me: str) -> dict[str, Any]:
    s = tp["summary"]
    games = s["games"] or 0
    cte, params = scope_cte(cfg, me)
    # Última evaluación conocida en las derrotas por tiempo: ¿estaba pareja o mejor?
    rows = con.execute(f"""
        WITH {cte},
        lost AS (SELECT game_id, color FROM scope WHERE status = 'outoftime' AND score = 0),
        last_eval AS (
            SELECT l.game_id, arg_max(CASE l.color WHEN 'white' THEN 1 ELSE -1 END * eval_cp(e.cp, e.mate), m.ply) AS ev
            FROM lost l JOIN moves m ON m.game_id = l.game_id JOIN evals e ON e.epd = m.epd_after
            GROUP BY l.game_id
        )
        SELECT l.game_id, le.ev FROM lost l LEFT JOIN last_eval le ON le.game_id = l.game_id
    """, params).fetchall()
    points = sum(0.5 if ev is None else win_pct(ev) / 100 for _, ev in rows)
    not_lost = sum(1 for _, ev in rows if ev is not None and ev >= -150)
    trouble_cost = 0.0
    if s["trouble_games"] and s["score_no_trouble_pct"] is not None and s["score_in_trouble_pct"] is not None:
        trouble_cost = s["trouble_games"] * max(0.0, s["score_no_trouble_pct"] - s["score_in_trouble_pct"]) / 100
    cost = _per100(max(points, trouble_cost), games)
    findings = []
    if s["lost_on_time"]:
        findings.append(f"{s['lost_on_time']} derrotas por tiempo ({s['lost_on_time_of_losses_pct']:.0f}% de tus "
                        f"derrotas); en {not_lost} la última evaluación conocida era pareja o mejor para vos.")
    if s["trouble_pct"]:
        findings.append(f"Te apurás (menos del {tp['thresholds']['trouble_pct']}% del reloj) en el "
                        f"{s['trouble_pct']:.0f}% de las partidas, en promedio en la jugada {s['avg_trouble_move']:.0f}; "
                        f"ahí hacés {s['score_in_trouble_pct']:.0f}% de los puntos (sin apuro, {s['score_no_trouble_pct']:.0f}%).")
    phases = {}
    for row in tp["by_phase"]:
        phases.setdefault(row["perf"], {})[row["phase"]] = row
    for perf, ph in phases.items():
        mid = ph.get("middlegame")
        if mid and mid["share_pct"] >= 60:
            findings.append(f"En {perf} usás el {mid['share_pct']:.0f}% de tu tiempo en el medio juego "
                            f"({mid['avg_seconds']:.1f} s por jugada): llegás al final con poco reloj.")
    tips = ["Poné un tope de tiempo por jugada en el medio juego y jugá rápido las posiciones conocidas.",
            "Con ventaja y poco reloj, simplificá: cambiar piezas reduce lo que hay que calcular."]
    return {"key": "time", "title": "Manejo del reloj", "cost_per_100": cost, "findings": findings, "tips": tips,
            "games": games}


def openings_area(con: duckdb.DuckDBPyConnection, cfg: Config, me: str, total_games: int) -> dict[str, Any]:
    rows = []
    for color in ("white", "black"):
        for o in opening_summary(con, cfg, me, color):
            if o["games"] < MIN_FAMILY_GAMES or o["avg_eval_out"] is None:
                continue
            # Cuánto peor que una posición igualada salgo, en puntos prácticos.
            deficit = ENGINE_WEIGHT * (50 - win_pct(o["avg_eval_out"])) / 100
            rows.append({**o, "color": color, "deficit": deficit,
                         "cost_per_100": _per100(max(0.0, deficit) * o["games"], total_games)})
    weak = sorted([r for r in rows if r["deficit"] > 0.02 or (r["over_pct"] or 0) <= -8],
                  key=lambda r: r["cost_per_100"], reverse=True)
    strong = sorted([r for r in rows if r["deficit"] < -0.02 and (r["over_pct"] or 0) >= 0],
                    key=lambda r: r["deficit"])
    cost = round(sum(r["cost_per_100"] for r in weak), 1)
    color_name = {"white": "blancas", "black": "negras"}
    findings = [f"Con {color_name[r['color']]} en {r['opening']} ({r['games']} partidas) salís de la apertura con "
                f"{r['avg_eval_out'] / 100:+.2f} de media y rendís {r['over_pct']:+.0f} puntos sobre tu Elo."
                for r in weak[:5]]
    tips = [f"Revisá la línea principal de {r['opening']} con {color_name[r['color']]}: dónde se te va la evaluación "
            f"(mirá tus posiciones flojas en el Explorador)." for r in weak[:2]]
    if strong:
        r = strong[0]
        tips.append(f"Tu mejor terreno: {r['opening']} con {color_name[r['color']]} "
                    f"(salís con {r['avg_eval_out'] / 100:+.2f}). Buscalo más seguido.")
    return {"key": "openings", "title": "Aperturas", "cost_per_100": cost, "findings": findings, "tips": tips,
            "weak": weak[:8], "strong": strong[:5]}


def conversion_area(con: duckdb.DuckDBPyConnection, cfg: Config, me: str) -> dict[str, Any]:
    cte, params = scope_cte(cfg, me)
    rows = fetch_dicts(con, f"""
        {_evaluated_moves_cte(cte)},
        per_game AS (
            SELECT game_id, any_value(score) AS score, max(before_me) AS best, min(before_me) AS worst
            FROM q WHERE before_me IS NOT NULL GROUP BY game_id
        )
        SELECT count(*) AS games_with_evals,
               count(*) FILTER (WHERE best >= {WINNING_CP}) AS winning,
               count(*) FILTER (WHERE best >= {WINNING_CP} AND score = 1) AS converted,
               sum(1 - score) FILTER (WHERE best >= {WINNING_CP}) AS points_dropped,
               count(*) FILTER (WHERE worst <= -{WINNING_CP}) AS losing,
               count(*) FILTER (WHERE worst <= -{WINNING_CP} AND score > 0) AS saved,
               sum(score) FILTER (WHERE worst <= -{WINNING_CP}) AS points_saved
        FROM per_game
    """, params)[0]
    n = rows["games_with_evals"] or 0
    findings = []
    if rows["winning"]:
        findings.append(f"Tuviste +3 o más en {rows['winning']} partidas y ganaste {rows['converted']} "
                        f"({100 * rows['converted'] / rows['winning']:.0f}%): se escaparon "
                        f"{rows['points_dropped']:.1f} puntos.")
    if rows["losing"]:
        findings.append(f"Estuviste −3 o peor en {rows['losing']} partidas y salvaste {rows['saved']} "
                        f"({rows['points_saved']:.1f} puntos).")
    # Parte de los puntos escapados es normal (blitz): se cuenta el exceso sobre convertir el 85%.
    excess = max(0.0, (rows["points_dropped"] or 0) - 0.15 * (rows["winning"] or 0))
    tips = ["Con ventaja grande, primero seguridad: frená el contrajuego, cambiá piezas y no regales tiempo.",
            "Repasá las partidas ganadas que no ganaste: casi siempre hay una jugada concreta donde se fue."]
    return {"key": "conversion", "title": "Convertir ventajas y defenderse", "cost_per_100": _per100(excess, n),
            "findings": findings, "tips": tips, **rows}


def errors_area(con: duckdb.DuckDBPyConnection, cfg: Config, me: str) -> dict[str, Any]:
    cte, params = scope_cte(cfg, me)
    base = _evaluated_moves_cte(cte)
    by_phase = fetch_dicts(con, f"""
        {base}
        SELECT phase, count(*) AS moves,
               round(100.0 * count(*) FILTER (WHERE judgment IN ('mistake', 'blunder')) / count(*), 1) AS error_rate_pct,
               round(100.0 * count(*) FILTER (WHERE judgment = 'blunder') / count(*), 1) AS blunder_rate_pct,
               round(avg(wp_loss), 2) AS avg_wp_loss, round(avg(least(cpl, 1000)), 1) AS avg_cpl
        FROM q WHERE mine AND judgment IS NOT NULL
        GROUP BY phase
        ORDER BY CASE phase WHEN 'opening' THEN 1 WHEN 'middlegame' THEN 2 ELSE 3 END
    """, params)
    # Oportunidades: el rival acaba de cometer un error (≥ 10 puntos) y le toca a mi jugada.
    opp = fetch_dicts(con, f"""
        {base}
        SELECT count(*) AS chances,
               count(*) FILTER (WHERE wp_loss >= 10) AS missed
        FROM q WHERE mine AND judgment IS NOT NULL AND prev_wp_loss >= 10
    """, params)[0]
    # Tipo de error: táctico (la mejor era captura/jaque, o mi jugada permite una captura/jaque ganador).
    mistakes = con.execute(f"""
        {base}
        SELECT q.epd_before, q.uci, q.best_uci, ea.best_uci, q.wp_loss, q.phase
        FROM q LEFT JOIN evals ea ON ea.epd = q.epd_after
        WHERE q.mine AND q.judgment IN ('mistake', 'blunder')
    """, params).fetchall()
    kinds: dict[str, int] = defaultdict(int)
    for epd, uci, best, reply, _wpl, _phase in mistakes:
        kinds[_error_kind(epd, uci, best, reply)] += 1
    total_mistakes = len(mistakes)
    findings = []
    if by_phase:
        worst = max(by_phase, key=lambda r: r["error_rate_pct"] if r["moves"] >= 50 else -1)
        if worst["moves"] >= 50:
            findings.append(f"Donde más te equivocás: {PHASES[worst['phase']].lower()} "
                            f"({worst['error_rate_pct']:.1f}% de las jugadas son errores, "
                            f"{worst['blunder_rate_pct']:.1f}% graves).")
    if total_mistakes:
        findings.append(f"De {total_mistakes} errores: {kinds['missed_tactic']} tácticas que no viste, "
                        f"{kinds['allowed_tactic']} tácticas que permitiste y {kinds['positional']} posicionales.")
    if opp["chances"]:
        findings.append(f"Cuando el rival se equivoca, no lo castigás en {opp['missed']} de {opp['chances']} "
                        f"ocasiones ({100 * opp['missed'] / opp['chances']:.0f}%).")
    tips = []
    if kinds["allowed_tactic"] >= kinds["missed_tactic"] and kinds["allowed_tactic"]:
        tips.append("Antes de mover, revisá capturas y jaques que le dejás al rival (chequeo de seguridad).")
    if kinds["missed_tactic"]:
        tips.append("Ejercicios de táctica cortos y diarios: la mitad de tus errores tácticos son jugadas forzantes que no viste.")
    if opp["chances"] and opp["missed"] / opp["chances"] > 0.4:
        tips.append("Cuando el rival hace una jugada rara o rápida, buscá primero cómo castigarla.")
    # Costo: pérdida media por error grave convertida a puntos (aprox.), sobre las partidas con evaluación.
    games = con.execute(f"{base} SELECT count(DISTINCT game_id) FROM q WHERE mine AND judgment IS NOT NULL",
                        params).fetchone()[0]
    blunder_points = sum(min(w, 50) for *_, w, _p in mistakes) / 100 * ENGINE_WEIGHT
    return {"key": "errors", "title": "Errores y táctica", "cost_per_100": _per100(blunder_points * 0.5, games),
            "findings": findings, "tips": tips, "by_phase": by_phase, "kinds": dict(kinds),
            "opportunities": opp, "games_with_evals": games}


def _error_kind(epd: str, uci: str, best: str | None, reply: str | None) -> str:
    try:
        board = chess.Board(epd + " 0 1")
        if best and best != uci:
            mv = chess.Move.from_uci(best)
            if mv in board.legal_moves and (board.is_capture(mv) or board.gives_check(mv) or mv.promotion):
                return "missed_tactic"
        board.push(chess.Move.from_uci(uci))
        if reply:
            mv = chess.Move.from_uci(reply)
            if mv in board.legal_moves and (board.is_capture(mv) or board.gives_check(mv) or mv.promotion):
                return "allowed_tactic"
    except ValueError:
        pass
    return "positional"


def tendencies_area(con: duckdb.DuckDBPyConnection, cfg: Config, me: str) -> dict[str, Any]:
    cte, params = scope_cte(cfg, me)
    rows = fetch_dicts(con, f"""
        WITH {cte},
        g AS (
            SELECT *, score - 1 / (1 + pow(10, (opp_rating - rating) / 400.0)) AS residual,
                   lag(score) OVER (ORDER BY created_at) AS prev_score,
                   date_diff('minute', lag(last_move_at) OVER (ORDER BY created_at), created_at) AS gap_min
            FROM scope WHERE rating IS NOT NULL AND opp_rating IS NOT NULL
        ),
        s AS (
            SELECT *, sum(CASE WHEN gap_min IS NULL OR gap_min > {SESSION_GAP_MIN} THEN 1 ELSE 0 END)
                       OVER (ORDER BY created_at) AS session
            FROM g
        ),
        n AS (SELECT *, row_number() OVER (PARTITION BY session ORDER BY created_at) AS nth FROM s)
        SELECT
            CASE WHEN gap_min <= {SESSION_GAP_MIN} AND prev_score = 0 THEN 'after_loss'
                 WHEN gap_min <= {SESSION_GAP_MIN} AND prev_score = 1 THEN 'after_win'
                 ELSE 'other' END AS k1,
            CASE WHEN nth >= 5 THEN 'long_session' ELSE 'early' END AS k2,
            count(*) AS games, sum(residual) AS residual, avg(score) AS score
        FROM n GROUP BY ALL
    """, params)

    def agg(key: str, value: str) -> dict[str, Any]:
        sel = [r for r in rows if r[key] == value]
        games = sum(r["games"] for r in sel)
        res = sum(r["residual"] for r in sel)
        return {"games": games, "over_pct": round(100 * res / games, 1) if games else None,
                "confidence": confidence(games)}

    after_loss, after_win = agg("k1", "after_loss"), agg("k1", "after_win")
    long_s, early = agg("k2", "long_session"), agg("k2", "early")
    findings, cost = [], 0.0
    total = sum(r["games"] for r in rows)
    if after_loss["games"] >= 20 and after_loss["over_pct"] is not None and after_loss["over_pct"] <= -4:
        findings.append(f"Después de perder (en la misma sesión) rendís {after_loss['over_pct']:+.0f} puntos sobre tu Elo "
                        f"({after_loss['games']} partidas); después de ganar, {after_win['over_pct'] or 0:+.0f}.")
        cost += -after_loss["over_pct"] * after_loss["games"] / 100
    if long_s["games"] >= 20 and long_s["over_pct"] is not None and long_s["over_pct"] <= -4:
        findings.append(f"Desde la 5ª partida de una sesión rendís {long_s['over_pct']:+.0f} puntos sobre tu Elo "
                        f"({long_s['games']} partidas).")
        cost += -long_s["over_pct"] * long_s["games"] / 100
    tips = []
    if findings:
        tips.append("Cortá la sesión después de dos derrotas seguidas o a las 4-5 partidas.")
    return {"key": "tendencies", "title": "Tendencias (tilt y sesiones)", "cost_per_100": _per100(cost, total),
            "findings": findings, "tips": tips, "after_loss": after_loss, "after_win": after_win,
            "long_session": long_s, "early_session": early}


def _endgame_type(epd: str) -> str:
    pieces = epd.split()[0]
    counts = {p: pieces.lower().count(p) for p in "qrbn"}
    if counts["q"]:
        return "Con damas"
    if counts["r"] and (counts["b"] or counts["n"]):
        return "Torres y piezas menores"
    if counts["r"]:
        return "De torres"
    if counts["b"] or counts["n"]:
        return "De piezas menores"
    return "De peones"


def endgames_area(con: duckdb.DuckDBPyConnection, cfg: Config, me: str) -> dict[str, Any]:
    cte, params = scope_cte(cfg, me)
    rows = con.execute(f"""
        WITH {cte}
        SELECT s.game_id, s.score, m.epd_before,
               s.score - 1 / (1 + pow(10, (s.opp_rating - s.rating) / 400.0)) AS residual
        FROM scope s JOIN moves m ON m.game_id = s.game_id AND m.ply = s.div_end
        WHERE s.div_end IS NOT NULL AND s.rating IS NOT NULL AND s.opp_rating IS NOT NULL
    """, params).fetchall()
    by_type: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for _gid, score, epd, res in rows:
        by_type[_endgame_type(epd)].append((score, res))
    types = sorted(({"type": t, "games": len(v), "score_pct": round(100 * sum(s for s, _ in v) / len(v), 1),
                     "over_pct": round(100 * sum(r for _, r in v) / len(v), 1), "confidence": confidence(len(v))}
                    for t, v in by_type.items()), key=lambda r: -r["games"])
    total = con.execute(f"WITH {cte} SELECT count(*) FROM scope", params).fetchone()[0]
    weak = [t for t in types if t["games"] >= 10 and t["over_pct"] <= -5]
    cost = sum(-t["over_pct"] * t["games"] / 100 for t in weak)
    findings = [f"Finales {t['type'].lower()}: {t['games']} partidas, {t['score_pct']:.0f}% de los puntos "
                f"({t['over_pct']:+.0f} sobre tu Elo)." for t in weak]
    tips = [f"Estudiá la técnica básica de los finales {weak[0]['type'].lower()}." ] if weak else []
    return {"key": "endgames", "title": "Finales", "cost_per_100": _per100(cost, total), "findings": findings,
            "tips": tips, "types": types}


def improvement_section(con: duckdb.DuckDBPyConnection, cfg: Config, me: str,
                        tp: dict[str, Any] | None = None) -> dict[str, Any]:
    """Todas las áreas, ordenadas por costo (puntos cada 100 partidas)."""
    tp = tp or time_profile(con, cfg, me)
    total = tp["summary"]["games"] or 0
    areas = [
        time_area(tp, con, cfg, me),
        openings_area(con, cfg, me, total),
        conversion_area(con, cfg, me),
        errors_area(con, cfg, me),
        tendencies_area(con, cfg, me),
        endgames_area(con, cfg, me),
    ]
    areas.sort(key=lambda a: a["cost_per_100"], reverse=True)
    cte, params = scope_cte(cfg, me)
    coverage = con.execute(f"""
        WITH {cte}
        SELECT count(*), count(e.epd) FROM scope s JOIN moves m ON m.game_id = s.game_id
        LEFT JOIN evals e ON e.epd = m.epd_after
    """, params).fetchone()
    return {
        "areas": areas,
        "priorities": [a["key"] for a in areas if a["cost_per_100"] >= 1 and a["findings"]][:3],
        "eval_coverage_pct": round(100 * coverage[1] / coverage[0], 1) if coverage[0] else 0,
        "games": total,
    }
