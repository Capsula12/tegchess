"""Nombres de apertura de Lichess (lichess-org/chess-openings, CC0) a partir de las posiciones.

Como Lichess, la apertura de una partida es la de la posición más profunda que figura en la lista;
al ir por posición (EPD) también se reconocen las transposiciones.
"""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

import chess

BOOK_DIR = Path(__file__).parent / "openings"


@lru_cache(maxsize=1)
def book() -> dict[str, tuple[str, str]]:
    """EPD -> (ECO, nombre). Si una posición aparece dos veces, gana la línea más corta (la más general)."""
    out: dict[str, tuple[str, str, int]] = {}
    for path in sorted(BOOK_DIR.glob("*.tsv")):
        with open(path, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                board = chess.Board()
                try:
                    for token in row["pgn"].split():
                        if token[0].isdigit():
                            continue
                        board.push_san(token)
                except ValueError:
                    continue
                epd = board.epd()
                plies = len(board.move_stack)
                if epd not in out or plies < out[epd][2]:
                    out[epd] = (row["eco"], row["name"], plies)
    return {epd: (eco, name) for epd, (eco, name, _) in out.items()}


def name_for(epds: list[str]) -> tuple[str, str, int] | None:
    """(ECO, nombre, ply) de la posición más profunda con nombre; epds[i] = posición tras el ply i+1."""
    b = book()
    for i in range(len(epds) - 1, -1, -1):
        hit = b.get(epds[i])
        if hit:
            return hit[0], hit[1], i + 1
    return None


def family(name: str) -> str:
    """"Sicilian Defense: Najdorf Variation" -> "Sicilian Defense"."""
    return name.split(":")[0].strip()
