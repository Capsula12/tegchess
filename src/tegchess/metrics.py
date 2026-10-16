"""Métricas de calidad de jugada (equivalente en Python de las macros SQL de db.py)."""

from __future__ import annotations

import math

CP_CAP = 1000

# Umbrales de Lichess sobre la pérdida de probabilidad de ganar (en puntos de %).
INACCURACY, MISTAKE, BLUNDER = 5.0, 10.0, 15.0


def eval_cp(cp: int | None, mate: int | None) -> int | None:
    """Evaluación en centipeones acotada a ±1000; el mate cuenta como ±1000."""
    if mate is not None:
        return CP_CAP if mate > 0 else -CP_CAP
    if cp is None:
        return None
    return max(-CP_CAP, min(CP_CAP, cp))


def win_pct(cp: float) -> float:
    """Probabilidad de ganar (0-100) según la fórmula de Lichess."""
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)


def move_loss(before_white: int, after_white: int, mover_is_white: bool) -> tuple[int, float]:
    """(pérdida de centipeones, pérdida de % de victoria) de una jugada, desde quien mueve."""
    sign = 1 if mover_is_white else -1
    cpl = max(0, sign * (before_white - after_white))
    wpl = max(0.0, win_pct(sign * before_white) - win_pct(sign * after_white))
    return cpl, wpl


def judgment(wp_loss: float) -> str:
    if wp_loss >= BLUNDER:
        return "blunder"
    if wp_loss >= MISTAKE:
        return "mistake"
    if wp_loss >= INACCURACY:
        return "inaccuracy"
    return "ok"
