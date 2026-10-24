"""Reconocer la posición de una captura de pantalla de un tablero digital (Lichess, chess.com…), sin IA.

1. Tablero: en una captura, las líneas entre casillas de un tablero son bordes donde el brillo cambia de
   signo fila por medio (clara→oscura, oscura→clara). Se suman esos bordes por columna y por fila y se busca
   la cuadrícula de 7 líneas internas igualmente espaciadas que mejor encaja (idea de tensorflow_chessbot,
   pero sin red neuronal).
2. Piezas: en cada casilla se separa la pieza del fondo (el color del fondo se toma de los bordes de la
   casilla, así no importa el tema del tablero ni si está resaltada) y se compara su silueta con las de un
   perfil: un juego de piezas visto una vez en la posición inicial. Viene uno con las piezas de Lichess y se
   puede calibrar otro (p.ej. chess.com) con una captura de la posición inicial.
3. Orientación: se deduce de dónde están los peones y los reyes (se puede forzar).

Siempre conviene revisar el resultado: la interfaz deja corregirlo antes de analizar.
"""

from __future__ import annotations

import base64
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import chess
import numpy as np
from PIL import Image

PROFILE_DIR = Path(__file__).parent / "vision_profiles"
GRID = 32                 # lado al que se lleva cada casilla para comparar
MARGIN = 0.10             # borde de cada casilla que no se mira (coordenadas, resaltados)
EMPTY_FRACTION = 0.04     # menos que esto de "pieza" en la casilla: vacía
PIECES = "PNBRQK"


# --- imagen ------------------------------------------------------------------------


def load_image(data: bytes | str | Path) -> np.ndarray:
    """RGB float (alto, ancho, 3) desde bytes, base64 (con o sin 'data:...') o una ruta."""
    if isinstance(data, Path) or (isinstance(data, str) and not data.startswith("data:") and len(data) < 1024):
        img = Image.open(data)
    else:
        if isinstance(data, str):
            data = base64.b64decode(data.split(",", 1)[-1])
        img = Image.open(io.BytesIO(data))
    return np.asarray(img.convert("RGB"), dtype=np.float32)


def _gray(rgb: np.ndarray) -> np.ndarray:
    return rgb @ np.array([0.299, 0.587, 0.114], dtype=np.float32)


# --- tablero -------------------------------------------------------------------------


def _grid_profile(gray: np.ndarray, axis: int) -> np.ndarray:
    """Fuerza de "línea de tablero" en cada columna (axis=1) o fila (axis=0)."""
    d = np.diff(gray, axis=axis)
    pos = np.clip(d, 0, None).sum(axis=1 - axis)
    neg = np.clip(-d, 0, None).sum(axis=1 - axis)
    prof = pos * neg   # alto solo si hay bordes de los dos signos: casillas que alternan
    return prof / (prof.max() or 1.0)


def _best_grid(prof: np.ndarray, min_sq: float, max_sq: float) -> tuple[float, float, float]:
    """(inicio de la 1ª línea interna, tamaño de casilla, puntaje) de 7 líneas igualmente espaciadas."""
    n = len(prof)
    # Máximo local en ±1 px para tolerar redondeos.
    pm = np.maximum(prof, np.maximum(np.roll(prof, 1), np.roll(prof, -1)))
    best = (0.0, 0.0, -1.0)
    offsets = np.arange(n, dtype=np.float32)
    for size in np.arange(min_sq, max_sq, 0.25):
        idx = offsets[:, None] + size * np.arange(7, dtype=np.float32)[None, :]
        valid = (offsets - size >= 0) & (idx[:, -1] + size < n)
        if not valid.any():
            continue
        vals = pm[np.clip(np.rint(idx).astype(int), 0, n - 1)]
        # Todas las líneas tienen que estar: el mínimo pesa (una sola línea fuerte no alcanza).
        score = vals.sum(axis=1) + 3 * vals.min(axis=1)
        score[~valid] = -1
        i = int(score.argmax())
        if score[i] > best[2]:
            best = (float(offsets[i]), float(size), float(score[i]))
    return best


@dataclass
class BoardBox:
    x0: float
    y0: float
    size: float      # lado de una casilla
    score: float

    def square(self, row: int, col: int) -> tuple[int, int, int, int]:
        """Caja (izq, arriba, der, abajo) de la casilla en la fila/columna de la imagen, sin el margen."""
        m = self.size * MARGIN
        left = self.x0 + col * self.size + m
        top = self.y0 + row * self.size + m
        return int(round(left)), int(round(top)), int(round(left + self.size - 2 * m)), int(round(top + self.size - 2 * m))


def find_board(rgb: np.ndarray) -> BoardBox:
    gray = _gray(rgb)
    h, w = gray.shape
    min_sq, max_sq = max(8.0, min(h, w) / 40), min(h, w) / 8 + 1
    x, sx, scx = _best_grid(_grid_profile(gray, 1), min_sq, max_sq)
    y, sy, scy = _best_grid(_grid_profile(gray, 0), min_sq, max_sq)
    if abs(sx - sy) > 0.1 * max(sx, sy):
        # Las casillas son cuadradas: repetir el eje más débil con el tamaño del más fuerte.
        size = sx if scx >= scy else sy
        if scx >= scy:
            y, sy, scy = _best_grid(_grid_profile(gray, 0), size - 1, size + 1.25)
        else:
            x, sx, scx = _best_grid(_grid_profile(gray, 1), size - 1, size + 1.25)
    size = (sx + sy) / 2
    # x, y son la primera línea interna: el tablero empieza una casilla antes (+0,5 px por np.diff).
    return BoardBox(x0=x - size + 0.5, y0=y - size + 0.5, size=size, score=(scx + scy) / 2)


# --- casillas ------------------------------------------------------------------------


def square_features(rgb: np.ndarray, box: BoardBox) -> list[dict[str, Any]]:
    """Por casilla (fila 0 = arriba de la imagen): silueta binaria GRID×GRID, fracción ocupada, brillo de la pieza."""
    img = Image.fromarray(rgb.astype(np.uint8))
    out = []
    for row in range(8):
        for col in range(8):
            crop = np.asarray(img.crop(box.square(row, col)).resize((GRID, GRID), Image.BILINEAR), dtype=np.float32)
            # Fondo: mediana del contorno de la casilla (ya sin margen).
            edge = np.concatenate([crop[0], crop[-1], crop[:, 0], crop[:, -1]])
            bg = np.median(edge, axis=0)
            diff = np.abs(crop - bg).sum(axis=2)
            mask = diff > 60
            # Las coordenadas (Lichess y chess.com las dibujan dentro de las casillas del borde) quedan en las
            # esquinas, adonde las piezas no llegan: se borran. Vacía u ocupada se decide mirando el centro.
            c = GRID * 7 // 32
            mask[:c, :c] = mask[:c, -c:] = mask[-c:, :c] = mask[-c:, -c:] = False
            frac = float(mask[c:-c, c:-c].mean())
            # "Brillo" de la pieza: parte de su silueta que es clara (0-255). Una pieza blanca tiene contorno y
            # detalles oscuros, y el rey negro una cruz blanca: la proporción los separa mejor que el promedio.
            lum = float(255 * (_gray(crop)[mask] > 128).mean()) if mask.any() else None
            out.append({"row": row, "col": col, "mask": mask, "frac": frac, "lum": lum, "bg": bg})
    return out


def _similarity(a: np.ndarray, b: np.ndarray) -> float:
    inter = np.logical_and(a, b).sum()
    union = np.logical_or(a, b).sum() or 1
    return float(inter / union)


# --- perfiles ------------------------------------------------------------------------


@dataclass
class Profile:
    name: str
    shapes: dict[str, list[np.ndarray]]     # "P".."K" -> siluetas de ejemplo (blancas y negras)
    white_lum: float                         # brillo medio típico de una pieza blanca (0-255)
    black_lum: float
    split: dict[str, float] | None = None    # umbral blanca/negra por tipo de pieza (el rey negro tiene cruz blanca)

    def is_white(self, kind: str, lum: float) -> bool:
        threshold = (self.split or {}).get(kind, (self.white_lum + self.black_lum) / 2)
        return lum > threshold

    def to_json(self) -> dict[str, Any]:
        enc = {p: [base64.b64encode(np.packbits(m.astype(np.uint8)).tobytes()).decode() for m in ms]
               for p, ms in self.shapes.items()}
        return {"name": self.name, "grid": GRID, "shapes": enc, "white_lum": self.white_lum,
                "black_lum": self.black_lum, "split": self.split}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Profile:
        grid = d.get("grid", GRID)
        shapes = {p: [np.unpackbits(np.frombuffer(base64.b64decode(s), dtype=np.uint8))[:grid * grid]
                      .reshape(grid, grid).astype(bool) for s in ms] for p, ms in d["shapes"].items()}
        return cls(d["name"], shapes, d["white_lum"], d["black_lum"], d.get("split"))


def list_profiles(extra_dir: Path | None = None) -> list[Profile]:
    out = []
    for folder in [PROFILE_DIR, extra_dir]:
        if folder and folder.exists():
            for f in sorted(folder.glob("*.json")):
                out.append(Profile.from_json(json.loads(f.read_text(encoding="utf-8"))))
    return out


def calibrate(rgb: np.ndarray, name: str) -> tuple[Profile, BoardBox, bool]:
    """Perfil a partir de una captura de la posición inicial. Devuelve (perfil, tablero, blancas abajo)."""
    box = find_board(rgb)
    feats = square_features(rgb, box)
    start = chess.Board()
    # Orientación: las piezas de la fila de abajo son más "claras" si las blancas están abajo.
    bottom = [f for f in feats if f["row"] >= 6 and f["lum"] is not None]
    top = [f for f in feats if f["row"] <= 1 and f["lum"] is not None]
    if len(bottom) < 12 or len(top) < 12:
        raise ValueError("No parece la posición inicial: faltan piezas en las dos primeras y últimas filas")
    white_bottom = np.mean([f["lum"] for f in bottom]) > np.mean([f["lum"] for f in top])
    shapes: dict[str, list[np.ndarray]] = {p: [] for p in PIECES}
    lums: dict[bool, list[float]] = {True: [], False: []}
    by_kind: dict[tuple[str, bool], list[float]] = {}
    for f in feats:
        sq = _square_at(f["row"], f["col"], white_bottom)
        piece = start.piece_at(sq)
        if piece is None:
            continue
        shapes[piece.symbol().upper()].append(f["mask"])
        lums[piece.color].append(f["lum"])
        by_kind.setdefault((piece.symbol().upper(), piece.color), []).append(f["lum"])
    split = {k: float((np.mean(by_kind[(k, True)]) + np.mean(by_kind[(k, False)])) / 2) for k in PIECES}
    profile = Profile(name, {p: ms[:4] for p, ms in shapes.items()},
                      float(np.mean(lums[True])), float(np.mean(lums[False])), split)
    return profile, box, bool(white_bottom)


def _square_at(row: int, col: int, white_bottom: bool) -> int:
    """Casilla de ajedrez en la fila/columna de la imagen."""
    return chess.square(col, 7 - row) if white_bottom else chess.square(7 - col, row)


# --- reconocer -------------------------------------------------------------------------


def recognize(rgb: np.ndarray, profiles: list[Profile], white_bottom: bool | None = None) -> dict[str, Any]:
    """Posición reconocida: FEN de las piezas, orientación, perfil usado y confianza por casilla."""
    box = find_board(rgb)
    feats = square_features(rgb, box)
    best = None
    for prof in profiles:
        cells, total = _classify(feats, prof)
        if best is None or total > best[2]:
            best = (prof, cells, total)
    if best is None:
        raise ValueError("No hay perfiles de piezas")
    prof, cells, total = best
    if white_bottom is None:
        white_bottom = _guess_orientation(cells)
    board = chess.Board(None)
    doubtful = []
    for c in cells:
        if c["piece"]:
            board.set_piece_at(_square_at(c["row"], c["col"], white_bottom), chess.Piece.from_symbol(c["piece"]))
            if c["score"] < 0.55:
                doubtful.append(chess.square_name(_square_at(c["row"], c["col"], white_bottom)))
    occupied = [c for c in cells if c["piece"]]
    last_move, turn = _last_move(feats, cells, white_bottom)
    return {
        "placement": board.board_fen(),
        "white_bottom": white_bottom,
        "profile": prof.name,
        "confidence": round(float(np.mean([c["score"] for c in occupied])) if occupied else 0.0, 2),
        "doubtful": doubtful,
        "board": {"x": box.x0, "y": box.y0, "size": box.size * 8, "score": round(box.score, 2)},
        "castling": _castling_guess(board),
        "last_move": last_move,
        "turn": turn,
    }


def _last_move(feats: list[dict[str, Any]], cells: list[dict[str, Any]], white_bottom: bool
               ) -> tuple[list[str] | None, str | None]:
    """Casillas resaltadas de la última jugada (fondo distinto al de su color) y, si se ve, de quién es el turno."""
    by_parity: dict[int, list[np.ndarray]] = {0: [], 1: []}
    for f in feats:
        by_parity[(f["row"] + f["col"]) % 2].append(f["bg"])
    medians = {k: np.median(np.array(v), axis=0) for k, v in by_parity.items()}
    marked = [f for f in feats if np.abs(f["bg"] - medians[(f["row"] + f["col"]) % 2]).sum() > 45]
    if len(marked) != 2:
        return None, None
    squares = [chess.square_name(_square_at(f["row"], f["col"], white_bottom)) for f in marked]
    pieces = [next(c["piece"] for c in cells if c["row"] == f["row"] and c["col"] == f["col"]) for f in marked]
    moved = [p for p in pieces if p]
    if len(moved) != 1:
        return squares, None
    # Movió el dueño de la pieza que quedó en una de las dos casillas: le toca al otro.
    return squares, "b" if moved[0].isupper() else "w"


def _classify(feats: list[dict[str, Any]], prof: Profile) -> tuple[list[dict[str, Any]], float]:
    cells, total = [], 0.0
    for f in feats:
        if f["frac"] < EMPTY_FRACTION:
            cells.append({"row": f["row"], "col": f["col"], "piece": None, "score": 1.0})
            continue
        scores = {p: max(_similarity(f["mask"], m) for m in ms) for p, ms in prof.shapes.items() if ms}
        kind = max(scores, key=scores.get)
        white = prof.is_white(kind, f["lum"])
        cells.append({"row": f["row"], "col": f["col"], "piece": kind if white else kind.lower(), "score": scores[kind]})
        total += scores[kind]
    return cells, total


def _guess_orientation(cells: list[dict[str, Any]]) -> bool:
    """Blancas abajo salvo que los reyes/peones digan lo contrario."""
    votes = 0.0
    for c in cells:
        p = c["piece"]
        if not p:
            continue
        lower_half = c["row"] >= 4
        weight = 3 if p in "Kk" else 1 if p in "Pp" else 0.3
        votes += weight if (p.isupper() == lower_half) else -weight
    return votes >= 0


def _castling_guess(board: chess.Board) -> str:
    rights = ""
    if board.piece_at(chess.E1) == chess.Piece(chess.KING, chess.WHITE):
        if board.piece_at(chess.H1) == chess.Piece(chess.ROOK, chess.WHITE):
            rights += "K"
        if board.piece_at(chess.A1) == chess.Piece(chess.ROOK, chess.WHITE):
            rights += "Q"
    if board.piece_at(chess.E8) == chess.Piece(chess.KING, chess.BLACK):
        if board.piece_at(chess.H8) == chess.Piece(chess.ROOK, chess.BLACK):
            rights += "k"
        if board.piece_at(chess.A8) == chess.Piece(chess.ROOK, chess.BLACK):
            rights += "q"
    return rights or "-"
