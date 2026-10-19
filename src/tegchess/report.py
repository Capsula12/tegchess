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
from tegchess.analysis.comparison import profile_comparison
from tegchess.analysis.expectation import (
    MIN_FAMILY_GAMES,
    MIN_POSITION_GAMES,
    matchup_base,
    strength,
    worth_correcting,
)
from tegchess.analysis.improvement import improvement_section
from tegchess.analysis.lines import line_to, san
from tegchess.analysis.openings import opening_summary, repeated_positions, repertoire_tree
from tegchess.analysis.prep import prep_section
from tegchess.analysis.traps import find_traps
from tegchess.analysis.scope import scope_cte
from tegchess.config import PROJECT_ROOT, Config, normalize_player, report_key
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
            "time": time_profile(con, cfg, player), "strength": strength(con, cfg, player)}


def weak_positions(con: duckdb.DuckDBPyConnection, cfg: Config, player: str, limit: int = 25) -> list[dict[str, Any]]:
    """Posiciones repetidas donde su jugada habitual pierde de verdad (≥ imprecisión) y el motor prefiere otra."""
    out = []
    fetch = limit * 4   # se piden de más: muchas no pasan el filtro
    rows = repeated_positions(con, cfg, player, limit=fetch)
    # Las evaluaciones de Lichess no traen la mejor jugada: se completan con Stockfish.
    if ensure_best_moves(con, cfg, [r["epd"] for r in rows if not r["best_uci"]]):
        rows = repeated_positions(con, cfg, player, limit=fetch)
    rows = [r for r in rows if (r["usual_count"] or 0) >= MIN_POSITION_GAMES
            and worth_correcting(r["usual_uci"], r["best_uci"], r["usual_wp_loss"])]
    rows.sort(key=lambda r: r["usual_wp_loss"] * r["usual_count"], reverse=True)
    for r in rows[:limit]:
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
                    my_color: str, base: float = 0.5) -> dict[str, Any]:
    """Cruce de mi repertorio (con my_color) contra el del rival (con el color contrario).

    base: puntaje esperado por Elo en este cruce (0-1); los esperados de posiciones y aperturas parten de ahí.
    """
    battles = select_battles(battle_positions(con, cfg, me, rival, my_color, base=base))
    for group in battles.values():
        attach_lines(con, group)
    reinforce = reinforce_positions(con, cfg, me, rival, my_color)
    if ensure_best_moves(con, cfg, [r["epd"] for r in reinforce if not r["best_uci"]]):
        reinforce = reinforce_positions(con, cfg, me, rival, my_color)
    attach_lines(con, reinforce)
    return {
        "families": family_matchup(con, cfg, me, rival, my_color, base=base),
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


def _sans(line: list[dict[str, Any]], until: int | None = None) -> str:
    """Jugadas numeradas: 1.e4 e5 2.Nf3 ..."""
    out = []
    for i, m in enumerate(line[:until]):
        out.append(f"{i // 2 + 1}.{m['san']}" if i % 2 == 0 else m["san"])
    return " ".join(out)


def _delta(x: dict[str, Any]) -> str:
    d = x.get("delta_pct")
    return "" if d is None else f", {d:+.0f} sobre lo esperado por Elo"


def build_plan(matchup: dict[str, Any] | None, traps: list[dict[str, Any]], time_hints: list[str] = (),
               prep: dict[str, Any] | None = None, base: dict[str, Any] | None = None
               ) -> dict[str, list[dict[str, Any]]]:
    """Recomendaciones concretas por color. Cada una puede traer línea y flechas para el tablero."""
    plan: dict[str, list[dict[str, Any]]] = {}
    for color in COLORS:
        m = matchup[color] if matchup else None
        opp = "negras" if color == "white" else "blancas"
        items: list[dict[str, Any]] = []
        if base:
            text = (f"Por Elo esperás {base['expected_pct']:.0f}% (vos {base['my_rating']}, él {base['rival_rating']} "
                    f"en {base['perf']}). Las recomendaciones apuntan a sumar puntos sobre esa base.")
            if base["big_gap"]:
                text += (" La diferencia es grande: prepará bien la apertura, porque es donde más se puede"
                         " achicar o estirar la distancia.")
            items.append({"kind": "base", "text": text})
        items += [{"kind": "tiempo", "text": h} for h in time_hints]

        pr = (prep or {}).get(color)
        if pr and pr.get("main_line"):
            ml = pr["main_line"]
            items.append({"kind": "preparar", "text":
                f"Con {opp} juega casi siempre {_sans(ml)} (llega a esa posición el {pr['main_line_reach_pct']:.0f}% "
                f"de las veces; elige su jugada habitual el {pr['predictability_pct']}% de las veces). "
                f"Hasta ahí vale la pena saberse la línea.",
                "line": ml, "shapes": []})
        for t in (pr or {}).get("targets", [])[:3]:
            known = " (ya las jugás)" if t.get("in_my_repertoire") else ""
            kind = "trampa" if t["kind"] == "trampa" else "preparar"
            items.append({"kind": kind, "text":
                f"Prepará {_sans(t['line'])}: él llega a esa posición el {t['reach_pct']:.0f}% de las veces y quedás "
                f"{t['eval_for_me'] / 100:+.2f} según el motor (+{t['gain_pct']:.0f} puntos de expectativa). "
                f"Son {t['my_moves']} jugadas tuyas para memorizar{known}.",
                "line": t["line"], "shapes": _arrow(t.get("best_reply_uci"), "green")})

        if m:
            fams = [f for f in m["families"]
                    if f["my_games"] >= MIN_FAMILY_GAMES and f["rival_games"] >= MIN_FAMILY_GAMES]
            for i, f in enumerate(fams[:2]):
                label = "Escenario más favorable" if i == 0 else "Otro escenario favorable"
                ev = "" if f["eval_for_me"] is None else f"salís de la apertura con {f['eval_for_me'] / 100:+.2f} de media; "
                items.append({"kind": "apertura", "text":
                    f"{label}: {f['opening']}. {ev.capitalize()}vos {f['my_score_pct']:.0f}% en {_games(f['my_games'])}, "
                    f"él {f['rival_score_pct']:.0f}% con {opp} en {_games(f['rival_games'])} "
                    f"(esperado {f['expected_pct']:.0f}%{_delta(f)}; confianza {f['confidence']})."})

            for bt in m["favorable"][:2]:
                opts = bt.get("options") or []
                if bt["to_move"] == "me" and opts:
                    o = opts[0]
                    rival_part = (f"él hace {o['rival_score_pct']:.0f}% después de {o['san']} ({_games(o['rival_games'])})"
                                  if o["rival_games"] else "él todavía no enfrentó esa jugada")
                    text = (f"{_where(bt)}, jugá {o['san']} (esperado {o['expected_pct']:.0f}%{_delta(o)}): vos "
                            f"{o['my_score_pct']:.0f}% en {_games(o['my_games'])}; {rival_part}.")
                    shapes = _arrow(o["uci"], "green")
                else:
                    usual = f" Ahí él suele jugar {bt['usual_san']}." if bt.get("usual_san") else ""
                    text = (f"{_where(bt)}: llevá la partida a esta posición (esperado {bt['expected_pct']:.0f}%"
                            f"{_delta(bt)}). Él llega en el {bt['rival_reach_pct'] or 0:.0f}% de sus partidas con {opp}.{usual}")
                    shapes = []
                items.append({"kind": "linea", "text": text, "line": bt["line"], "shapes": shapes})

        for t in [t for t in traps if t["punisher_color"] == color][:3]:
            where = "en tu repertorio" if t["in_my_repertoire"] else "fuera de tu repertorio habitual"
            punished = (f" Sus rivales lo castigaron {t['refutation_found']} de {t['games_after_mistake']} veces"
                        f" y él igual hizo {t['rival_score_pct']:.0f}%." if t["games_after_mistake"] else "")
            items.append({"kind": "trampa", "text":
                f"Trampa ({where}, refutación {t['difficulty']}): {_where(t, capital=False)} juega {t['usual_san']} "
                f"{t['usual_count']} de {t['occurrences']} veces (llega a la posición en el {t['reach_pct']:.0f}% de sus "
                f"partidas con {opp}). Castigalo con {t['refutation_san']}"
                + (f" ({t['refutation_line']})." if t["refutation_line"] else ".") + punished,
                "line": t["line"], "idx": t["line_mistake_index"], "shapes": _arrow(t["refutation_uci"], "green")})

        if m:
            for bt in m["danger"][:2]:
                items.append({"kind": "evitar", "text":
                    f"{_where(bt)}: evitá esta posición (esperado {bt['expected_pct']:.0f}%{_delta(bt)}). Vos "
                    f"{bt['my_score_pct']:.0f}% en {_games(bt['my_games'])}, él {bt['rival_score_pct']:.0f}% en "
                    f"{_games(bt['rival_games'])}.",
                    "line": bt["line"], "shapes": []})

            for r in [r for r in m["reinforce"] if worth_correcting(r["usual_uci"], r["best_uci"],
                                                                     r["usual_wp_loss"])][:2]:
                items.append({"kind": "reforzar", "text":
                    f"Reforzá: {_where(r, capital=False)} solés jugar {r['usual_san']} "
                    f"{r['usual_count']} veces (pierde {r['usual_wp_loss']:.0f} puntos de % de victoria); el motor "
                    f"prefiere {r['best_san']}. "
                    f"Él llega a esta posición en {_games(r['rival_games'])}.",
                    "line": r["line"], "shapes": _arrow(r["usual_uci"], "red") + _arrow(r["best_uci"], "green")})
            for f in [f for f in m["families"] if f["reinforce"] and f["my_games"] >= MIN_FAMILY_GAMES
                      and f["rival_games"] >= MIN_FAMILY_GAMES][:2]:
                items.append({"kind": "reforzar", "text":
                    f"Reforzá {f['opening']}: tu pérdida media en la apertura es {f['my_cpl']:.0f} cp y la de él "
                    f"{f['rival_cpl']:.0f} cp."})
        plan[color] = items
    return plan


ME_KEY = "_me"   # archivo del perfil propio (los ids de jugador nunca empiezan con "_")


def _tree_epds(node: dict[str, Any]) -> set[str]:
    out = {" ".join(node["fen"].split()[:4])}
    for child in node["children"]:
        out |= _tree_epds(child)
    return out


def tree_overlap(con: duckdb.DuckDBPyConnection, cfg: Config, other: str, other_color: str,
                 tree: dict[str, Any]) -> dict[str, int]:
    """EPD -> cuántas partidas de `other` (con `other_color`) pasan por cada posición del árbol."""
    epds = list(_tree_epds(tree))
    cte, params = scope_cte(cfg, other, other_color)
    out: dict[str, int] = {}
    for i in range(0, len(epds), 1000):
        chunk = epds[i:i + 1000]
        marks = ",".join("?" for _ in chunk)
        out.update(dict(con.execute(f"""
            WITH {cte},
            pos AS (
                SELECT s.game_id, m.epd_after AS epd FROM scope s JOIN moves m ON m.game_id = s.game_id
                WHERE m.epd_after IN ({marks})
                UNION
                SELECT s.game_id, m.epd_before FROM scope s JOIN moves m ON m.game_id = s.game_id
                WHERE m.ply = 1 AND m.epd_before IN ({marks})
            )
            SELECT epd, count(DISTINCT game_id) FROM pos GROUP BY epd
        """, [*params, *chunk, *chunk]).fetchall()))
    return out


def me_section(con: duckdb.DuckDBPyConnection, cfg: Config, me: str) -> dict[str, Any]:
    """Mi sección (igual para todos los informes): se calcula una vez por corrida."""
    return player_section(con, cfg, me)


def build_me_report(con: duckdb.DuckDBPyConnection, cfg: Config, cache: dict[str, Any] | None = None) -> dict[str, Any]:
    """Mi perfil general: mis datos, qué mejorar y la comparación con todos mis rivales."""
    me = normalize_player(cfg.me)
    cache = cache if cache is not None else {}
    if "me" not in cache:
        cache["me"] = me_section(con, cfg, me)
    sec = cache["me"]
    return {
        "meta": {
            "version": __version__,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "me": me, "me_name": cfg.me, "key": ME_KEY, "source": cfg.source,
            "rivals": [cfg.display_name(normalize_player(o)) for o in cfg.opponents],
            "analysed_plies": cfg.engine.max_ply,
        },
        "me": sec,
        "improvement": improvement_section(con, cfg, me, sec["profile"]["time"]),
        "comparison": profile_comparison(con, cfg),
    }


def build_report(con: duckdb.DuckDBPyConnection, cfg: Config, rival: str,
                 cache: dict[str, Any] | None = None) -> dict[str, Any]:
    """Informe de un rival. `cache` (opcional) guarda mi sección entre rivales."""
    rival = normalize_player(rival)
    me = normalize_player(cfg.me) if cfg.me else None
    has_me = bool(me and me != rival)
    rival_section = player_section(con, cfg, rival)
    rival_section["traps"] = find_traps(con, cfg, rival, me if has_me else None)
    cache = cache if cache is not None else {}
    if has_me and "me" not in cache:
        cache["me"] = me_section(con, cfg, me)
    me_sec = cache.get("me") if has_me else None
    base = matchup_base(me_sec["profile"]["strength"], rival_section["profile"]["strength"]) if me_sec else None
    base_score = base["expected_pct"] / 100 if base else 0.5
    matchup = {c: matchup_section(con, cfg, me, rival, c, base_score) for c in COLORS} if has_me else None
    prep = {c: prep_section(con, cfg, rival, c, me if has_me else None) for c in COLORS}
    # Explorador: en cada posición del árbol de uno, cuántas veces llegó el otro (con el color contrario).
    overlap = None
    if has_me:
        opp = {"white": "black", "black": "white"}
        overlap = {
            "rival": {c: tree_overlap(con, cfg, me, opp[c], rival_section["trees"][c]) for c in COLORS},
            "me": {c: tree_overlap(con, cfg, rival, opp[c], me_sec["trees"][c]) for c in COLORS},
        }
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
            "key": report_key(rival),
            "source": cfg.source,
            "rival": rival,
            "rival_name": cfg.display_name(rival),
            "perf_types": [] if cfg.is_pgn else cfg.download.perf_types,
            "since": str(cfg.download.since) if cfg.download.since and not cfg.is_pgn else None,
            "until": str(cfg.download.until) if cfg.download.until and not cfg.is_pgn else None,
            "max_games": None if cfg.is_pgn else cfg.download.max_games,
            "engine_depth": cfg.engine.depth,
            "analysed_plies": cfg.engine.max_ply,
            "eval_coverage_pct": round(100 * eval_coverage[0] / eval_coverage[1], 1) if eval_coverage[1] else 0,
        },
        "rival": rival_section,
        "me": me_sec,
        "base": base,
        "prep": prep,
        "tree_overlap": overlap,
        "matchup": matchup,
        "plan": build_plan(matchup, rival_section["traps"], rival_section["profile"]["time"]["hints"], prep, base),
    }


def _json_default(o: Any) -> Any:
    if isinstance(o, datetime):
        return o.isoformat()
    return str(o)


def write_report(report: dict[str, Any], reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    key = report["meta"].get("key") or report_key(report["meta"]["rival"])
    path = reports_dir / f"{key}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, default=_json_default), encoding="utf-8")
    return path


def export_static(reports: list[dict[str, Any]], out_dir: Path) -> Path:
    """Copia la interfaz web con los informes incrustados: se abre con doble clic, sin servidor."""
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(WEB_DIR, out_dir)
    payload = {r["meta"].get("key") or report_key(r["meta"]["rival"]): r for r in reports}
    data_js = "window.TEGCHESS_STATIC = " + json.dumps(payload, ensure_ascii=False, default=_json_default) + ";\n"
    (out_dir / "static-data.js").write_text(data_js, encoding="utf-8")
    return out_dir / "index.html"
