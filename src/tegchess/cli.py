"""Línea de comandos: tegchess fetch | analyze | report | serve."""

from __future__ import annotations

import logging

import typer
from rich.console import Console
from rich.logging import RichHandler

from tegchess.config import find_stockfish, lichess_token, load_config

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
def doctor() -> None:
    """Muestra la configuración detectada (usuarios, Stockfish, token)."""
    cfg = load_config()
    console.print(f"Yo: [bold]{cfg.me or '(sin definir)'}[/]  Rivales: {', '.join(cfg.opponents) or '(ninguno)'}")
    console.print(f"Base de datos: {cfg.db_path}")
    console.print(f"Stockfish: {find_stockfish(cfg.engine.path) or '[red]no encontrado[/]'}")
    console.print(f"Token de Lichess: {'sí' if lichess_token() else 'no'}")
