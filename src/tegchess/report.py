"""Arma el informe de un rival como JSON (consumido por web/ y reutilizable en la etapa 2)."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from tegchess import __version__
from tegchess.analysis.crossover import (
    attach_lines,
    battle_positions,
    family_matchup,
    reinforce_positions,
    select_battles,
)
from tegchess.analysis.clock import time_profile
from tegchess.analysis.lines import line_to, san
from tegchess.analysis.openings import opening_summary, repeated_positions, repertoire_tree
from tegchess.analysis.traps import find_traps
from tegchess.analysis.scope import scope_cte
from tegchess.config import PROJECT_ROOT, Config
from tegchess.db import fetch_dicts
from tegchess.engine import ensure_best_moves, epd_to_fen

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
    return {"summary": summary, "by_color": by_color, "by_perf": by_perf, "endings": endings,
            "time": time_profile(con, cfg, player)}


def weak_positions(con: duckdb.DuckDBPyConnection, cfg: Config, player: str, limit: int = 25) -> list[dict[str, Any]]:
    """Posiciones repetidas del jugador con su jugada habitual vs. la del motor, y la línea para llegar."""
    out = []
    rows = repeated_positions(con, cfg, player, limit=limit)
    # Las evaluaciones de Lichess no traen la mejor jugada: se completan con Stockfish.
    if ensure_best_moves(con, cfg, [r["epd"] for r in rows if not r["best_uci"]]):
        rows = repeated_positions(con, cfg, player, limit=limit)
    for r in rows:
        # Sin pérdida, o su jugada habitual ya es la del motor y pierde poco: no es una debilidad.
        if (r["avg_wp_loss"] or 0) < 2 or (r["usual_uci"] == r["best_uci"] and r["avg_wp_loss"] < 5):
            continue
        sample = r["sample_games"][0]
        ply = con.execute(
            "SELECT ply FROM moves WHERE game_id = ? AND epd_before = ? ORDER BY ply LIMIT 1", [sample, r["epd"]]
        ).fetchone()[0]
        out.append({
            **r,
            "fen": epd_to_fen(r["epd"]),
            "best_san": san(r["epd"], r["best_uci"]),
            "line": line_to(con, sample, ply),
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


def matchup_section(con: duckdb.DuckDBPyConnection, cfg: Config, me: str, rival: str,
                    my_color: str) -> dict[str, Any]:
    """Cruce de mi repertorio (con my_color) contra el del rival (con el color contrario)."""
    battles = select_battles(battle_positions(con, cfg, me, rival, my_color))
    for group in battles.values():
        attach_lines(con, group)
    reinforce = reinforce_positions(con, cfg, me, rival, my_color)
    if ensure_best_moves(con, cfg, [r["epd"] for r in reinforce if not r["best_uci"]]):
        reinforce = reinforce_positions(con, cfg, me, rival, my_color)
    attach_lines(con, reinforce)
    return {
        "families": family_matchup(con, cfg, me, rival, my_color),
        "favorable": battles["favorable"],
        "danger": battles["danger"],
        "reinforce": reinforce,
    }


def _arrow(uci: str | None, brush: str) -> list[dict[str, str]]:
    return [{"orig": uci[:2], "dest": uci[2:4], "brush": brush}] if uci else []


def _games(n: int) -> str:
    return f"{n} partida" if n == 1 else f"{n} partidas"


def _where(item: dict[str, Any], capital: bool = True) -> str:
    prefix = "En" if capital else "en"
    return f"{prefix} {item['opening']}" if item.get("opening") else f"{prefix} esta posición"


def build_plan(matchup: dict[str, Any], traps: list[dict[str, Any]],
               time_hints: list[str] = ()) -> dict[str, list[dict[str, Any]]]:
    """Recomendaciones concretas por color. Cada una puede traer línea y flechas para el tablero."""
    plan: dict[str, list[dict[str, Any]]] = {}
    for color in COLORS:
        m = matchup[color]
        opp = "negras" if color == "white" else "blancas"
        items: list[dict[str, Any]] = [{"kind": "tiempo", "text": h} for h in time_hints]

        fams = [f for f in m["families"] if f["my_games"] >= 3 and f["rival_games"] >= 2]
        for i, f in enumerate(fams[:2]):
            label = "Escenario más favorable" if i == 0 else "Otro escenario favorable"
            items.append({"kind": "apertura", "text":
                f"{label}: {f['opening']}. Vos {f['my_score_pct']:.0f}% en {_games(f['my_games'])}; "
                f"él {f['rival_score_pct']:.0f}% con {opp} en {_games(f['rival_games'])} "
                f"(esperado {f['expected_pct']:.0f}%)."})

        for bt in m["favorable"][:3]:
            opts = bt.get("options") or []
            if bt["to_move"] == "me" and opts:
                o = opts[0]
                rival_part = (f"él hace {o['rival_score_pct']:.0f}% después de {o['san']} ({_games(o['rival_games'])})"
                              if o["rival_games"] else "él todavía no enfrentó esa jugada")
                text = (f"{_where(bt)}, jugá {o['san']}: vos {o['my_score_pct']:.0f}% en {_games(o['my_games'])}; "
                        f"{rival_part}.")
                shapes = _arrow(o["uci"], "green")
            else:
                usual = f" Ahí él suele jugar {bt['usual_san']}." if bt.get("usual_san") else ""
                text = (f"{_where(bt)}: llevá la partida a esta posición (esperado {bt['expected_pct']:.0f}%). "
                        f"Vos {bt['my_score_pct']:.0f}% en {_games(bt['my_games'])}, él "
                        f"{bt['rival_score_pct']:.0f}% en {_games(bt['rival_games'])}.{usual}")
                shapes = []
            items.append({"kind": "linea", "text": text, "line": bt["line"], "shapes": shapes})

        for t in [t for t in traps if t["punisher_color"] == color][:3]:
            where = "en tu repertorio" if t["in_my_repertoire"] else "fuera de tu repertorio habitual"
            punished = (f" Sus rivales lo castigaron {t['refutation_found']} de {t['games_after_mistake']} veces"
                        f" y él igual hizo {t['rival_score_pct']:.0f}%." if t["games_after_mistake"] else "")
            items.append({"kind": "trampa", "text":
                f"Trampa ({where}, refutación {t['difficulty']}): {_where(t, capital=False)} juega {t['usual_san']} "
                f"{t['usual_count']} de {t['occurrences']} veces. Castigalo con {t['refutation_san']}"
                + (f" ({t['refutation_line']})." if t["refutation_line"] else ".") + punished,
                "line": t["line"], "idx": t["line_mistake_index"], "shapes": _arrow(t["refutation_uci"], "green")})

        for bt in m["danger"][:2]:
            items.append({"kind": "evitar", "text":
                f"{_where(bt)}: evitá esta posición (esperado {bt['expected_pct']:.0f}%). Vos "
                f"{bt['my_score_pct']:.0f}% en {_games(bt['my_games'])}, él {bt['rival_score_pct']:.0f}% en "
                f"{_games(bt['rival_games'])}.",
                "line": bt["line"], "shapes": []})

        for r in m["reinforce"][:2]:
            items.append({"kind": "reforzar", "text":
                f"Reforzá: {_where(r, capital=False)} solés jugar {r['usual_san']} "
                f"(pierde {r['avg_wp_loss']:.0f} puntos de % de victoria); el motor prefiere {r['best_san'] or '–'}. "
                f"Él llega a esta posición en {_games(r['rival_games'])}.",
                "line": r["line"], "shapes": _arrow(r["usual_uci"], "red") + _arrow(r["best_uci"], "green")})
        for f in [f for f in m["families"] if f["reinforce"]][:2]:
            items.append({"kind": "reforzar", "text":
                f"Reforzá {f['opening']}: tu pérdida media en la apertura es {f['my_cpl']:.0f} cp y la de él "
                f"{f['rival_cpl']:.0f} cp."})
        plan[color] = items
    return plan


def build_report(con: duckdb.DuckDBPyConnection, cfg: Config, rival: str) -> dict[str, Any]:
    rival = rival.lower()
    me = cfg.me.lower() if cfg.me else None
    has_me = bool(me and me != rival)
    rival_section = player_section(con, cfg, rival)
    rival_section["traps"] = find_traps(con, cfg, rival, me if has_me else None)
    matchup = {c: matchup_section(con, cfg, me, rival, c) for c in COLORS} if has_me else None
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
            "me_name": cfg.me if has_me else None,
            "rival": rival,
            "rival_name": next((o for o in cfg.opponents if o.lower() == rival), rival),
            "perf_types": cfg.download.perf_types,
            "since": str(cfg.download.since) if cfg.download.since else None,
            "until": str(cfg.download.until) if cfg.download.until else None,
            "max_games": cfg.download.max_games,
            "engine_depth": cfg.engine.depth,
            "analysed_plies": cfg.engine.max_ply,
            "eval_coverage_pct": round(100 * eval_coverage[0] / eval_coverage[1], 1) if eval_coverage[1] else 0,
        },
        "rival": rival_section,
        "me": player_section(con, cfg, me) if has_me else None,
        "matchup": matchup,
        "plan": build_plan(matchup, rival_section["traps"], rival_section["profile"]["time"]["hints"])
        if matchup else None,
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
