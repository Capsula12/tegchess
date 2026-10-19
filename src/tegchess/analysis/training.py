"""Entrenar aperturas: ejercicios sacados de mis partidas y repetición espaciada.

Cada ejercicio es una posición en la que me toca mover:
- "principal": posiciones de mi repertorio a las que llego seguido. La respuesta es mi jugada habitual,
  salvo que pierda según el motor (≥ imprecisión): entonces la respuesta es la del motor.
- "desvio": posiciones a las que llego menos (cuando el rival se sale de mi línea principal) y donde
  mi jugada habitual pierde: ahí es donde más se escapa la apertura.
- "rival": por informe, sus trampas (castigar su error habitual) y sus líneas de preparación.

El progreso (cajas de Leitner) se guarda aparte, en data/training.json, por modo (Lichess / PGN):
así no choca con la base mientras corre un análisis.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import chess
import duckdb

from tegchess.analysis.expectation import MIN_MOVE_LOSS, MIN_POSITION_GAMES
from tegchess.analysis.lines import line_to_position, pv_san, san
from tegchess.analysis.openings import repeated_positions
from tegchess.config import Config
from tegchess.engine import ensure_best_moves, epd_to_fen

MAX_PLY = 24              # hasta la jugada 12
MAIN_MIN_GAMES = 5        # "línea principal": llego al menos 5 veces
MAX_EXERCISES = 150
# Días hasta el próximo repaso según la caja (0 = nueva).
INTERVALS = [0, 1, 3, 7, 14, 30, 60]


def dests(fen: str) -> dict[str, list[str]]:
    """Jugadas legales para el tablero (casilla de origen -> destinos)."""
    board = chess.Board(fen)
    out: dict[str, list[str]] = {}
    for move in board.legal_moves:
        out.setdefault(chess.square_name(move.from_square), []).append(chess.square_name(move.to_square))
    return out


def exercise(epd: str, line: list[dict[str, Any]], color: str, answer: str, kind: str, **extra: Any
             ) -> dict[str, Any] | None:
    """Un ejercicio listo para la interfaz, o None si la respuesta no es legal en la posición."""
    fen = epd_to_fen(epd)
    answer_san = san(epd, answer)
    if not answer_san:
        return None
    return {"id": f"{kind}|{epd}", "epd": epd, "fen": fen, "color": color, "kind": kind, "line": line,
            "answer_uci": answer, "answer_san": answer_san, "dests": dests(fen), **extra}


def repertoire_exercises(con: duckdb.DuckDBPyConnection, cfg: Config, me: str) -> list[dict[str, Any]]:
    rows = []
    for color in ("white", "black"):
        rows += repeated_positions(con, cfg, me, color=color, min_occurrences=MIN_POSITION_GAMES,
                                   max_ply=MAX_PLY, limit=400)
    if ensure_best_moves(con, cfg, [r["epd"] for r in rows if not r["best_uci"]]):
        rows = []
        for color in ("white", "black"):
            rows += repeated_positions(con, cfg, me, color=color, min_occurrences=MIN_POSITION_GAMES,
                                       max_ply=MAX_PLY, limit=400)
    pvs = _pvs(con, [r["epd"] for r in rows])
    out = []
    for r in rows:
        usual, best, loss = r["usual_uci"], r["best_uci"], r["usual_wp_loss"] or 0
        if not usual:
            continue
        wrong = bool(best) and best != usual and loss >= MIN_MOVE_LOSS
        main = r["occurrences"] >= MAIN_MIN_GAMES
        if not main and not wrong:
            continue   # en las líneas secundarias solo se entrena lo que juego mal
        answer = best if wrong else usual
        line = line_to_position(con, r["epd"])
        if not line:
            continue   # la posición inicial no es un ejercicio
        ex = exercise(
            r["epd"], line, r["color"], answer,
            "principal" if main else "desvio",
            opening=r["opening"], occurrences=r["occurrences"], ply=r["ply"],
            usual_uci=usual, usual_san=r["usual_san"], usual_count=r["usual_count"],
            usual_wp_loss=round(loss, 1), corrects_usual=wrong,
            accepted=[answer] + ([best] if best and best != answer and not wrong and loss < 2 else []),
            # La variante del motor solo cuando corrige mi jugada (si no, puede seguir otra idea).
            pv=pv_san(r["epd"], pvs.get(r["epd"]), ply_before=r["ply"] - 1) if wrong else None,
            priority=round(r["occurrences"] * (1 + (loss if wrong else 0) / 5), 1),
        )
        if ex:
            out.append(ex)
    out.sort(key=lambda e: e["priority"], reverse=True)
    return out[:MAX_EXERCISES]


def rival_exercises(traps: list[dict[str, Any]], prep: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Para entrenar antes de jugarle: castigar sus trampas y seguir las líneas de preparación."""
    out = []
    for t in traps:
        after = t["line"][t["line_mistake_index"]] if len(t["line"]) > t["line_mistake_index"] else None
        if not after:
            continue
        board = chess.Board(after["fen"])
        ex = exercise(board.epd(), t["line"][:t["line_mistake_index"] + 1], t["punisher_color"], t["refutation_uci"],
                      "rival", opening=t["opening"], source="trampa", occurrences=t["occurrences"],
                      note=f"Acaba de jugar {t['usual_san']} (lo hace {t['usual_count']} de {t['occurrences']} veces). "
                           f"Castigalo.", accepted=[t["refutation_uci"]], pv=t.get("refutation_line"))
        if ex:
            out.append(ex)
    for color, section in (prep or {}).items():
        for target in section.get("targets", []):
            if not target.get("best_reply_uci") or not target["line"]:
                continue
            board = chess.Board(target["line"][-1]["fen"])
            ex = exercise(board.epd(), target["line"], color, target["best_reply_uci"], "rival",
                          source="preparación", occurrences=target["rival_games"],
                          note=f"Línea de preparación: él llega acá el {target['reach_pct']:.0f}% de las veces. "
                               f"¿Cómo seguís?", accepted=[target["best_reply_uci"]])
            if ex:
                out.append(ex)
    return out


def _pvs(con: duckdb.DuckDBPyConnection, epds: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for i in range(0, len(epds), 1000):
        chunk = epds[i:i + 1000]
        out.update(dict(con.execute(
            f"SELECT epd, pv FROM evals WHERE pv IS NOT NULL AND epd IN ({','.join('?' for _ in chunk)})", chunk
        ).fetchall()))
    return out


# --- repetición espaciada -----------------------------------------------------------


def review(card: dict[str, Any] | None, result: str, now: datetime | None = None) -> dict[str, Any]:
    """Leitner: si acierto sube de caja y el próximo repaso se aleja; si fallo vuelve a la caja 1 (mañana)."""
    now = now or datetime.now(timezone.utc)
    card = dict(card or {"box": 0, "right": 0, "wrong": 0})
    if result == "right":
        card["box"] = min(card.get("box", 0) + 1, len(INTERVALS) - 1)
        card["right"] = card.get("right", 0) + 1
    else:
        card["box"] = 1
        card["wrong"] = card.get("wrong", 0) + 1
    card["due"] = (now + timedelta(days=INTERVALS[card["box"]])).isoformat(timespec="seconds")
    card["last"] = now.isoformat(timespec="seconds")
    card["last_result"] = result
    return card


class TrainingStore:
    """data/training.json: {modo: {id de ejercicio: tarjeta}}. Escritura atómica y con candado."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def load(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def cards(self, mode: str) -> dict[str, Any]:
        return self.load().get(mode, {})

    def record(self, mode: str, exercise_id: str, result: str) -> dict[str, Any]:
        with self._lock:
            data = self.load()
            cards = data.setdefault(mode, {})
            cards[exercise_id] = review(cards.get(exercise_id), result)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".training_", suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, self.path)
            return cards[exercise_id]
