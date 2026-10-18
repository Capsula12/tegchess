"""Línea de comandos: tegchess fetch | analyze | report | export | serve."""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn

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


@contextmanager
def rich_progress() -> Iterator[Callable[[str, int | None], Callable[[], None]]]:
    with Progress(
        TextColumn("{task.description}"), BarColumn(), MofNCompleteColumn(), TimeElapsedColumn(),
        console=console,
    ) as progress:

        def make(desc: str, total: int | None) -> Callable[[], None]:
            task = progress.add_task(desc, total=total)
            return lambda: progress.advance(task)

        yield make


@app.command()
def fetch(
    users: list[str] = typer.Argument(None, help="Usuarios a descargar (por defecto: yo + rivales del config)"),
) -> None:
    """Descarga (incremental) las partidas a DuckDB; en modo PGN, importa la carpeta de PGN."""
    from tegchess.pipeline import fetch_all

    cfg = load_config()
    if not cfg.is_pgn and not (users or cfg.players()):
        console.print("[red]No hay usuarios: completá 'me' y 'opponents' en config.yaml[/]")
        raise typer.Exit(1)
    with rich_progress() as progress:
        result = fetch_all(cfg, users, progress)
    if cfg.is_pgn:
        summary = result["pgn"]
        for name, n in summary["files"].items():
            console.print(f"  {name}: " + (f"{n} partidas" if isinstance(n, int) else n))
        for name in summary["removed"]:
            console.print(f"  {name}: [yellow]ya no está en la carpeta, se borraron sus partidas[/]")
        console.print(f"Carpeta: {summary['folder']} · {summary['players']} jugadores")
        return
    for user, n in result.items():
        console.print(f"  {user}: " + (f"{n} partidas nuevas" if isinstance(n, int) else f"[red]{n}[/]"))


@app.command()
def analyze(
    depth: int = typer.Option(None, help="Profundidad de Stockfish (por defecto la del config)"),
    max_ply: int = typer.Option(None, help="Analizar hasta este ply (0 = partida completa)"),
    no_cloud: bool = typer.Option(False, "--no-cloud", help="No usar la nube de Lichess"),
) -> None:
    """Evalúa las posiciones pendientes (Lichess, nube de Lichess y Stockfish)."""
    from tegchess.pipeline import analyze as run_analyze

    cfg = load_config()
    if depth is not None:
        cfg.engine.depth = depth
    if max_ply is not None:
        cfg.engine.max_ply = max_ply
    if no_cloud:
        cfg.engine.use_cloud_eval = False
    try:
        with rich_progress() as progress:
            stats = run_analyze(cfg, progress)
    except KeyboardInterrupt:
        console.print("[yellow]Interrumpido: lo evaluado hasta ahora quedó guardado.[/]")
        raise typer.Exit(130)
    console.print(
        f"Evaluadas: {stats['stockfish']} con Stockfish, {stats['cloud']} desde la nube, "
        f"{stats['terminal']} terminales."
    )


@app.command()
def report(
    rivals: list[str] = typer.Argument(None, help="Rivales (por defecto: los del config)"),
    export: bool = typer.Option(True, help="Exportar también el HTML estático"),
) -> None:
    """Genera los informes JSON (data/reports) y el HTML estático (data/export)."""
    from tegchess.pipeline import build_reports, export_html

    cfg = load_config()
    with rich_progress() as progress:
        paths = build_reports(cfg, rivals, progress)
    for p in paths:
        console.print(f"  {p}")
    if export:
        console.print(f"HTML estático: {export_html(cfg)}")


@app.command()
def serve(
    port: int = typer.Option(8765, help="Puerto local"),
    no_browser: bool = typer.Option(False, "--no-browser", help="No abrir el navegador"),
) -> None:
    """Levanta la interfaz web en http://127.0.0.1:PUERTO."""
    import threading
    import webbrowser

    import uvicorn

    from tegchess.server import create_app

    url = f"http://127.0.0.1:{port}"
    console.print(f"Interfaz en [bold]{url}[/] (Ctrl+C para salir)")
    if not no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")


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
    cfg = load_config().active()
    console.print(f"Origen de las partidas: [bold]{'archivos PGN (' + str(cfg.pgn_path) + ')' if cfg.is_pgn else 'Lichess'}[/]")
    console.print(f"Yo: [bold]{cfg.me or '(sin definir)'}[/]  Rivales: {', '.join(cfg.opponents) or '(ninguno)'}")
    console.print(f"Base de datos: {cfg.db_path}")
    console.print(f"Stockfish: {find_stockfish(cfg.engine.path) or '[red]no encontrado[/]'}")
    console.print(f"Token de Lichess: {'sí' if lichess_token() else 'no'}")
