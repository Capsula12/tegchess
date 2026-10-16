"""Línea de comandos: tegchess fetch | analyze | report | serve."""

from __future__ import annotations

import logging
import sys

import typer
from rich.console import Console
from rich.logging import RichHandler

from tegchess.config import find_stockfish, lichess_token, load_config

# La consola de Windows (o una salida redirigida) puede venir en cp1252: forzamos UTF-8.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

app = typer.Typer(help="Preparación de rivales con Lichess + Stockfish", no_args_is_help=True)
console = Console()


@app.callback()
def main(verbose: bool = typer.Option(False, "--verbose", "-v", help="Logging detallado")) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(console=console, show_path=False, rich_tracebacks=True)],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


@app.command()
def fetch(
    users: list[str] = typer.Argument(None, help="Usuarios a descargar (por defecto: yo + rivales del config)"),
) -> None:
    """Descarga (incremental) las partidas a DuckDB."""
    from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

    from tegchess import db
    from tegchess.ingest import sync_user
    from tegchess.lichess import LichessClient, LichessError

    cfg = load_config()
    users = users or cfg.players()
    if not users:
        console.print("[red]No hay usuarios: completá 'me' y 'opponents' en config.yaml[/]")
        raise typer.Exit(1)
    con = db.connect(cfg.db_path)
    with LichessClient(lichess_token()) as client, Progress(
        SpinnerColumn(), TextColumn("{task.description}"), TextColumn("{task.completed} partidas"),
        TimeElapsedColumn(), console=console,
    ) as progress:
        for user in users:
            task = progress.add_task(f"Descargando {user}", total=None)
            try:
                n = sync_user(client, con, cfg, user, on_game=lambda: progress.advance(task))
            except LichessError as e:
                progress.update(task, description=f"[red]{user}: {e}[/]")
                continue
            progress.update(task, description=f"[green]{user}[/]: {n} nuevas")
    con.close()


@app.command()
def doctor() -> None:
    """Muestra la configuración detectada (usuarios, Stockfish, token)."""
    cfg = load_config()
    console.print(f"Yo: [bold]{cfg.me or '(sin definir)'}[/]  Rivales: {', '.join(cfg.opponents) or '(ninguno)'}")
    console.print(f"Base de datos: {cfg.db_path}")
    console.print(f"Stockfish: {find_stockfish(cfg.engine.path) or '[red]no encontrado[/]'}")
    console.print(f"Token de Lichess: {'sí' if lichess_token() else 'no'}")
