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
def openings(
    user: str = typer.Argument(..., help="Usuario de Lichess"),
    color: str = typer.Option("white", "--color", "-c", help="white | black"),
    detail: bool = typer.Option(False, "--detail", "-d", help="Agrupar por variante y no por familia"),
    top: int = typer.Option(15, help="Cantidad de filas"),
) -> None:
    """Resumen de aperturas de un jugador con resultados y pérdida media."""
    from rich.table import Table

    from tegchess import db
    from tegchess.analysis.openings import opening_summary

    cfg = load_config()
    con = db.connect(cfg.db_path, read_only=True)
    rows = opening_summary(con, cfg, user, color, by="name" if detail else "family")
    con.close()
    table = Table(title=f"{user} con {'blancas' if color == 'white' else 'negras'}")
    for col in ("Apertura", "ECO", "Partidas", "G-T-P", "% puntos", "Elo rival", "CPL apertura"):
        table.add_column(col, justify="left" if col == "Apertura" else "right")
    for r in rows[:top]:
        table.add_row(
            r["opening"], r["eco"] or "", str(r["games"]), f"{r['wins']}-{r['draws']}-{r['losses']}",
            f"{r['score_pct']:.0f}", f"{r['avg_opp_rating'] or 0:.0f}",
            "" if r["avg_cpl"] is None else f"{r['avg_cpl']:.0f} ({r['games_with_evals']})",
        )
    console.print(table)


@app.command()
def doctor() -> None:
    """Muestra la configuración detectada (usuarios, Stockfish, token)."""
    cfg = load_config()
    console.print(f"Yo: [bold]{cfg.me or '(sin definir)'}[/]  Rivales: {', '.join(cfg.opponents) or '(ninguno)'}")
    console.print(f"Base de datos: {cfg.db_path}")
    console.print(f"Stockfish: {find_stockfish(cfg.engine.path) or '[red]no encontrado[/]'}")
    console.print(f"Token de Lichess: {'sí' if lichess_token() else 'no'}")
