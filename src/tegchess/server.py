"""Servidor local: sirve web/ y una API JSON para informes, configuración y tareas largas."""

from __future__ import annotations

import json
import logging
import threading
import time
import traceback
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from tegchess import pipeline
from tegchess.pdf import PDF_DIR, export_pdfs
from tegchess.config import Config, find_stockfish, lichess_token, load_config, normalize_player, report_key, save_config
from tegchess.pgn_import import search_players
from tegchess.report import WEB_DIR

log = logging.getLogger(__name__)


class JobRequest(BaseModel):
    action: str                        # fetch | analyze | report | all | pdf
    users: list[str] | None = None


class JobRunner:
    """Una tarea a la vez (DuckDB admite un solo escritor). El front consulta el estado."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.state: dict[str, Any] = {"running": False, "action": None, "steps": [], "error": None,
                                      "result": None, "started_at": None, "finished_at": None}

    def progress(self, desc: str, total: int | None) -> Callable[[], None]:
        step = {"desc": desc, "done": 0, "total": total}
        self.state["steps"].append(step)

        def tick() -> None:
            step["done"] += 1

        return tick

    def start(self, action: str, users: list[str] | None, base_url: str = "") -> None:
        if not self._lock.acquire(blocking=False):
            raise HTTPException(409, "Ya hay una tarea en curso")
        self.state = {"running": True, "action": action, "steps": [], "error": None, "result": None,
                      "started_at": time.time(), "finished_at": None}
        threading.Thread(target=self._run, args=(action, users, base_url), daemon=True).start()

    def _run(self, action: str, users: list[str] | None, base_url: str) -> None:
        try:
            cfg = load_config()
            result: dict[str, Any] = {}
            if action in ("fetch", "all"):
                result["fetch"] = pipeline.fetch_all(cfg, users, self.progress)
            if action in ("analyze", "all"):
                result["analyze"] = pipeline.analyze(cfg, self.progress)
            if action in ("report", "all"):
                result["report"] = [p.name for p in pipeline.build_reports(cfg, users, self.progress)]
                result["export"] = str(pipeline.export_html(cfg))
            if action == "pdf":
                # No toca la base: imprime los informes JSON ya generados.
                active = cfg.active()
                result["pdf"] = export_pdfs(active, users or [report_key(o.lower()) for o in active.opponents],
                                            base_url, self.progress)
                result["pdf_dir"] = str(PDF_DIR)
            if action not in ("fetch", "analyze", "report", "all", "pdf"):
                raise ValueError(f"Acción desconocida: {action}")
            self.state["result"] = result
        except Exception as e:  # se muestra en la interfaz
            log.error("Falló la tarea %s: %s", action, e)
            self.state["error"] = f"{e}\n\n{traceback.format_exc(limit=3)}"
        finally:
            self.state["running"] = False
            self.state["finished_at"] = time.time()
            self._lock.release()


def create_app() -> FastAPI:
    app = FastAPI(title="tegchess", docs_url="/api/docs")
    jobs = JobRunner()

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        cfg = load_config().active()
        players_file = cfg.data_path / "pgn_players.json"
        return {
            "source": cfg.source,
            "me": cfg.me,
            "opponents": cfg.opponents,
            "opponent_keys": [report_key(normalize_player(o)) for o in cfg.opponents],
            "pgn_folder": str(cfg.pgn_path),
            "pgn_players_indexed": players_file.exists(),
            "stockfish": find_stockfish(cfg.engine.path),
            "token": bool(lichess_token()),
            "db_exists": cfg.db_path.exists(),
        }

    @app.get("/api/config")
    def get_config() -> dict[str, Any]:
        return load_config().model_dump(mode="json")

    @app.put("/api/config")
    def put_config(data: dict[str, Any]) -> dict[str, Any]:
        try:
            cfg = Config.model_validate(data)
        except ValidationError as e:
            raise HTTPException(422, str(e)) from e
        save_config(cfg)
        return cfg.model_dump(mode="json")

    @app.get("/api/reports")
    def list_reports() -> list[dict[str, Any]]:
        cfg = load_config()
        out = []
        for p in sorted(cfg.reports_path.glob("*.json")):
            meta = json.loads(p.read_text(encoding="utf-8")).get("meta", {})
            out.append({"rival": p.stem, "name": meta.get("rival_name"), "generated_at": meta.get("generated_at")})
        return out

    @app.get("/api/players")
    def players(q: str = "", limit: int = 30) -> list[dict[str, Any]]:
        """Buscador de jugadores de los PGN importados (índice en data/pgn_players.json)."""
        return search_players(load_config(), q, min(limit, 200))

    @app.get("/api/reports/{rival}")
    def get_report(rival: str) -> FileResponse:
        cfg = load_config()
        path = (cfg.reports_path / f"{rival.lower()}.json").resolve()
        if path.parent != cfg.reports_path.resolve() or not path.exists():
            raise HTTPException(404, "No hay informe para ese rival (generalo primero)")
        return FileResponse(path, media_type="application/json")

    @app.post("/api/jobs")
    def start_job(req: JobRequest, request: Request) -> dict[str, Any]:
        jobs.start(req.action, req.users, str(request.base_url))
        return jobs.state

    @app.get("/api/jobs")
    def job_state() -> dict[str, Any]:
        return jobs.state

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    app.mount("/", StaticFiles(directory=WEB_DIR), name="web")
    return app
