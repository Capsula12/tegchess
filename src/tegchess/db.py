"""Esquema DuckDB y utilidades de acceso.

Convenciones:
- Las posiciones se identifican por EPD (FEN sin contadores de jugadas), así la misma
  posición alcanzada en partidas distintas es una sola fila en `evals`.
- Las evaluaciones se guardan siempre desde el punto de vista de las blancas.
- Los usuarios se guardan en minúsculas (id de Lichess).
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id               VARCHAR PRIMARY KEY,
    rated            BOOLEAN,
    variant          VARCHAR,
    speed            VARCHAR,
    perf             VARCHAR,
    created_at       TIMESTAMP,
    last_move_at     TIMESTAMP,
    status           VARCHAR,
    winner           VARCHAR,          -- 'white' | 'black' | NULL (tablas)
    white            VARCHAR,          -- id en minúsculas
    black            VARCHAR,
    white_name       VARCHAR,
    black_name       VARCHAR,
    white_rating     INTEGER,
    black_rating     INTEGER,
    white_title      VARCHAR,
    black_title      VARCHAR,
    eco              VARCHAR,
    opening_name     VARCHAR,
    opening_family   VARCHAR,          -- nombre antes de ':' (p.ej. "Sicilian Defense")
    opening_ply      INTEGER,
    clock_initial    INTEGER,          -- segundos
    clock_increment  INTEGER,          -- segundos
    div_middle       INTEGER,          -- ply donde empieza el medio juego (Lichess)
    div_end          INTEGER,          -- ply donde empieza el final (Lichess)
    num_plies        INTEGER,
    has_analysis     BOOLEAN,
    white_accuracy   INTEGER,
    black_accuracy   INTEGER,
    source           VARCHAR,
    fetched_at       TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS moves (
    game_id     VARCHAR,
    ply         SMALLINT,              -- 1 = primera jugada de las blancas
    color       VARCHAR,               -- quién mueve: 'white' | 'black'
    san         VARCHAR,
    uci         VARCHAR,
    epd_before  VARCHAR,
    epd_after   VARCHAR,
    clock_cs    INTEGER,               -- reloj restante tras la jugada (centésimas)
    spent_cs    INTEGER,               -- tiempo gastado en la jugada (centésimas)
    is_capture  BOOLEAN,
    is_check    BOOLEAN,
    piece       VARCHAR,               -- p, n, b, r, q, k
    PRIMARY KEY (game_id, ply)
);

CREATE TABLE IF NOT EXISTS evals (
    epd         VARCHAR PRIMARY KEY,
    cp          INTEGER,               -- centipeones (blancas), NULL si hay mate
    mate        INTEGER,               -- mate en N (positivo = ganan blancas)
    best_uci    VARCHAR,
    pv          VARCHAR,               -- variante principal en UCI separada por espacios
    depth       INTEGER,
    source      VARCHAR,               -- 'lichess' | 'cloud' | 'stockfish' | 'terminal'
    lines       JSON,                  -- multipv: [{"uci":..,"cp":..,"mate":..,"pv":..}]
    updated_at  TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS sync_state (
    key                VARCHAR PRIMARY KEY,   -- usuario|perfs|rated
    username           VARCHAR,
    newest_ms          BIGINT,
    oldest_ms          BIGINT,
    backfill_since_ms  BIGINT,                -- desde dónde se completó hacia atrás
    backfill_done      BOOLEAN,
    updated_at         TIMESTAMP DEFAULT current_timestamp
);

-- Archivos PGN importados (modo PGN): si un archivo cambia o desaparece, se reimportan o borran sus partidas.
CREATE TABLE IF NOT EXISTS pgn_files (
    path        VARCHAR PRIMARY KEY,
    size        BIGINT,
    mtime       DOUBLE,
    games       INTEGER,
    imported_at TIMESTAMP DEFAULT current_timestamp
);

CREATE INDEX IF NOT EXISTS idx_moves_before ON moves (epd_before);
CREATE INDEX IF NOT EXISTS idx_games_white ON games (white);
CREATE INDEX IF NOT EXISTS idx_games_black ON games (black);
"""

# Macros y vistas: la lógica de métricas vive en SQL para poder consultarla directo
# (y reutilizarla en la etapa 2). metrics.py tiene el equivalente en Python para tests.
VIEWS = """
CREATE OR REPLACE MACRO eval_cp(cp, mate) AS
    CASE
        WHEN mate IS NOT NULL THEN CASE WHEN mate > 0 THEN 1000 ELSE -1000 END
        WHEN cp IS NULL THEN NULL
        ELSE greatest(-1000, least(1000, cp))
    END;

CREATE OR REPLACE MACRO win_pct(cp) AS
    50 + 50 * (2 / (1 + exp(-0.00368208 * cp)) - 1);

CREATE OR REPLACE VIEW player_games AS
SELECT g.*, g.id AS game_id, 'white' AS color, g.white AS player, g.black AS opponent,
       g.white_rating AS rating, g.black_rating AS opp_rating, g.white_accuracy AS accuracy,
       CAST(CASE g.winner WHEN 'white' THEN 1 WHEN 'black' THEN 0 ELSE 0.5 END AS DOUBLE) AS score
FROM games g
UNION ALL
SELECT g.*, g.id AS game_id, 'black' AS color, g.black AS player, g.white AS opponent,
       g.black_rating AS rating, g.white_rating AS opp_rating, g.black_accuracy AS accuracy,
       CAST(CASE g.winner WHEN 'black' THEN 1 WHEN 'white' THEN 0 ELSE 0.5 END AS DOUBLE) AS score
FROM games g;

CREATE OR REPLACE VIEW move_quality AS
WITH base AS (
    SELECT m.*,
           CASE m.color WHEN 'white' THEN 1 ELSE -1 END AS sign,
           eval_cp(eb.cp, eb.mate) AS before_cp,
           eval_cp(ea.cp, ea.mate) AS after_cp,
           eb.best_uci AS best_uci
    FROM moves m
    LEFT JOIN evals eb ON eb.epd = m.epd_before
    LEFT JOIN evals ea ON ea.epd = m.epd_after
)
SELECT *,
       greatest(0, sign * (before_cp - after_cp)) AS cpl,
       greatest(0, win_pct(sign * before_cp) - win_pct(sign * after_cp)) AS wp_loss,
       CASE
           WHEN before_cp IS NULL OR after_cp IS NULL THEN NULL
           WHEN win_pct(sign * before_cp) - win_pct(sign * after_cp) >= 15 THEN 'blunder'
           WHEN win_pct(sign * before_cp) - win_pct(sign * after_cp) >= 10 THEN 'mistake'
           WHEN win_pct(sign * before_cp) - win_pct(sign * after_cp) >= 5 THEN 'inaccuracy'
           ELSE 'ok'
       END AS judgment
FROM base;
"""


def connect(db_path: Path | str, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    db_path = Path(db_path)
    if not read_only:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path), read_only=read_only)
    if not read_only:
        init_schema(con)
    return con


# Columnas agregadas después de la primera versión (bases ya creadas).
MIGRATIONS = """
ALTER TABLE games ADD COLUMN IF NOT EXISTS origin VARCHAR DEFAULT 'lichess';  -- 'lichess' | 'pgn'
ALTER TABLE games ADD COLUMN IF NOT EXISTS pgn_file VARCHAR;                  -- archivo de origen (modo PGN)
"""


def init_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(SCHEMA)
    con.execute(MIGRATIONS)
    con.execute(VIEWS)


# Tipos para read_json: así DuckDB no tiene que inferirlos y respeta el esquema.
_JSON_COLUMNS: dict[str, dict[str, str]] = {}


def _columns_for(con: duckdb.DuckDBPyConnection, table: str) -> dict[str, str]:
    if table not in _JSON_COLUMNS:
        rows = con.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = ? ORDER BY ordinal_position",
            [table],
        ).fetchall()
        _JSON_COLUMNS[table] = {name: dtype for name, dtype in rows}
    return _JSON_COLUMNS[table]


def bulk_insert(
    con: duckdb.DuckDBPyConnection,
    table: str,
    rows: Iterable[dict[str, Any]],
    *,
    on_conflict: str = "ignore",
) -> int:
    """Inserta muchas filas rápido pasando por un NDJSON temporal.

    on_conflict: 'ignore' (no pisa lo existente) o 'replace'.
    """
    rows = list(rows)
    if not rows:
        return 0
    all_cols = _columns_for(con, table)
    cols = [c for c in all_cols if c in rows[0]]
    columns_sql = "{" + ", ".join(f"'{c}': '{all_cols[c]}'" for c in cols) + "}"
    fd, tmp = tempfile.mkstemp(suffix=".ndjson", prefix=f"tegchess_{table}_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps({c: r.get(c) for c in cols}, default=str))
                f.write("\n")
        verb = "INSERT OR REPLACE" if on_conflict == "replace" else "INSERT OR IGNORE"
        col_list = ", ".join(cols)
        con.execute(
            f"{verb} INTO {table} ({col_list}) "
            f"SELECT {col_list} FROM read_json(?, format='newline_delimited', columns={columns_sql})",
            [tmp.replace("\\", "/")],
        )
    finally:
        os.unlink(tmp)
    return len(rows)


def fetch_dicts(con: duckdb.DuckDBPyConnection, sql: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
    cur = con.execute(sql, params or [])
    names = [d[0] for d in cur.description]
    return [{n: float(v) if isinstance(v, Decimal) else v for n, v in zip(names, row)}
            for row in cur.fetchall()]
