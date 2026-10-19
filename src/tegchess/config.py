"""Carga de configuración (config.yaml) y secretos (.env / variables de entorno)."""

from __future__ import annotations

import os
import re
import shutil
import unicodedata
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config.yaml"
EXAMPLE_CONFIG = PROJECT_ROOT / "config.example.yaml"


class DownloadConfig(BaseModel):
    perf_types: list[str] = Field(default_factory=lambda: ["blitz", "rapid"])
    max_games: int | None = 500
    since: date | None = None
    until: date | None = None
    rated_only: bool = True


class EngineConfig(BaseModel):
    path: str | None = None
    depth: int = 16
    workers: int | None = None
    threads_per_worker: int = 1
    hash_mb: int = 64
    max_ply: int = 30
    # Mis partidas se analizan completas (fases, conversión, tipos de error en "Mi juego").
    analyse_my_games_fully: bool = True
    use_cloud_eval: bool = True
    cloud_eval_max_ply: int = 16
    # La nube de Lichess es para "unas pocas posiciones": se piden solo las más repetidas y con tope.
    cloud_eval_min_count: int = 5
    cloud_eval_max_positions: int = 300
    cloud_eval_delay_s: float = 0.3

    def resolved_workers(self) -> int:
        if self.workers:
            return self.workers
        return max(1, (os.cpu_count() or 2) - 2)


class AnalysisConfig(BaseModel):
    min_occurrences: int = 3
    opening_depth_plies: int = 12


class PgnConfig(BaseModel):
    """Modo PGN: partidas de archivos (p.ej. exportadas de ChessBase) en vez de Lichess."""
    folder: str = "pgn"                 # relativa a data_dir (o absoluta)
    me: str = ""                        # mi nombre como figura en los PGN ("Apellido, Nombre")
    opponents: list[str] = Field(default_factory=list)


def normalize_player(name: str) -> str:
    """Id de jugador: minúsculas y espacios normalizados ("Perez,Juan " -> "perez, juan").

    Para Lichess es el usuario en minúsculas; para los PGN, el nombre sin tildes (las bases mezclan
    "Pérez" y "Perez" para la misma persona).
    """
    name = "".join(c for c in unicodedata.normalize("NFD", name) if unicodedata.category(c) != "Mn")
    name = re.sub(r"\s+", " ", name.strip().lower())
    return re.sub(r"\s*,\s*", ", ", name)


def report_key(player_id: str) -> str:
    """Nombre de archivo seguro para el informe de un jugador (igual al id en Lichess)."""
    return re.sub(r"[^\w-]+", "_", player_id).strip("_") or "_"


class Config(BaseModel):
    source: Literal["lichess", "pgn"] = "lichess"
    me: str = ""
    opponents: list[str] = Field(default_factory=list)
    download: DownloadConfig = Field(default_factory=DownloadConfig)
    engine: EngineConfig = Field(default_factory=EngineConfig)
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    pgn: PgnConfig = Field(default_factory=PgnConfig)
    data_dir: str = "data"

    @property
    def is_pgn(self) -> bool:
        return self.source == "pgn"

    def active(self) -> Config:
        """La config con "yo" y los rivales del modo elegido (los análisis trabajan siempre con esta)."""
        if not self.is_pgn:
            return self
        return self.model_copy(update={"me": self.pgn.me, "opponents": list(self.pgn.opponents)})

    @property
    def pgn_path(self) -> Path:
        p = Path(self.pgn.folder).expanduser()
        return p if p.is_absolute() else self.data_path / p

    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir).expanduser()
        return p if p.is_absolute() else PROJECT_ROOT / p

    @property
    def db_path(self) -> Path:
        return self.data_path / "tegchess.duckdb"

    @property
    def reports_path(self) -> Path:
        # Cada modo tiene sus informes: no se mezclan escalas de Elo ni rivales.
        return self.data_path / ("reports-pgn" if self.is_pgn else "reports")

    def players(self) -> list[str]:
        """Yo + rivales, sin duplicados y normalizados (Lichess no distingue mayúsculas)."""
        seen: dict[str, None] = {}
        for u in [self.me, *self.opponents]:
            if u:
                seen.setdefault(normalize_player(u), None)
        return list(seen)

    def display_name(self, player_id: str) -> str:
        """El nombre como figura en la configuración (con mayúsculas), o el id."""
        return next((o for o in [self.me, *self.opponents] if o and normalize_player(o) == player_id), player_id)


def config_path() -> Path:
    """config.yaml (o el indicado en TEGCHESS_CONFIG); si no existe, el de ejemplo."""
    env = os.environ.get("TEGCHESS_CONFIG")
    if env:
        return Path(env)
    return DEFAULT_CONFIG if DEFAULT_CONFIG.exists() else EXAMPLE_CONFIG


def load_config(path: Path | None = None) -> Config:
    path = path or config_path()
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return Config.model_validate(raw)


def save_config(cfg: Config, path: Path | None = None) -> None:
    path = path or Path(os.environ.get("TEGCHESS_CONFIG") or DEFAULT_CONFIG)
    data = cfg.model_dump(mode="json")
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def load_dotenv(path: Path | None = None) -> None:
    """Lee KEY=VALUE de .env sin pisar variables ya definidas."""
    path = path or PROJECT_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def lichess_token() -> str | None:
    load_dotenv()
    return os.environ.get("LICHESS_TOKEN") or None


def find_stockfish(configured: str | None = None) -> str | None:
    """Busca Stockfish: ruta configurada, PATH, instalación de winget."""
    if configured:
        return configured if Path(configured).exists() else shutil.which(configured)
    for name in ("stockfish", "stockfish.exe"):
        found = shutil.which(name)
        if found:
            return found
    # winget instala los paquetes portables por usuario o, con --scope machine, en Program Files.
    roots = [(os.environ.get("LOCALAPPDATA"), ("Microsoft", "WinGet", "Packages")),
             (os.environ.get("ProgramFiles"), ("WinGet", "Packages"))]
    for base, parts in roots:
        if not base:
            continue
        pkgs = Path(base).joinpath(*parts)
        if pkgs.exists():
            for exe in sorted(pkgs.glob("Stockfish.Stockfish*/**/stockfish*.exe")):
                return str(exe)
    return None
