"""Servidor local: sirve web/ y una API JSON para informes, configuración y tareas largas."""

from __future__ import annotations

import json
from pathlib import Path
import logging
import threading
import time
import traceback
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from tegchess import pipeline, position, vision
from tegchess.pdf import PDF_DIR, export_pdfs
from tegchess.config import Config, find_stockfish, lichess_token, load_config, normalize_player, report_key, save_config
from tegchess.analysis.training import TrainingStore
from tegchess.pgn_import import search_players
from tegchess.report import WEB_DIR

log = logging.getLogger(__name__)


class ImageRequest(BaseModel):
    image: str                         # captura en base64 (data URL)
    white_bottom: bool | None = None   # forzar la orientación
    name: str | None = None            # para calibrar un perfil nuevo


class PositionRequest(BaseModel):
    fen: str
    multipv: int = 3


class TrainingResult(BaseModel):
    id: str
    result: str                        # right | wrong


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


class NoCache(BaseHTTPMiddleware):
    """Sin caché: los informes y la interfaz cambian al regenerar (si no, el navegador muestra lo viejo)."""

    async def dispatch(self, request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response


def create_app() -> FastAPI:
    app = FastAPI(title="tegchess", docs_url="/api/docs")
    app.add_middleware(NoCache)
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
            if p.stem.startswith("_"):   # perfil propio (/api/me)
                continue
            meta = json.loads(p.read_text(encoding="utf-8")).get("meta", {})
            out.append({"rival": p.stem, "name": meta.get("rival_name"), "generated_at": meta.get("generated_at")})
        return out

    @app.get("/api/me")
    def me_report() -> FileResponse:
        path = load_config().active().reports_path / "_me.json"
        if not path.exists():
            raise HTTPException(404, "Todavía no hay perfil propio: generá los informes")
        return FileResponse(path, media_type="application/json")

    def training_store() -> tuple[TrainingStore, str]:
        cfg = load_config()
        return TrainingStore(cfg.data_path / "training.json"), cfg.source

    @app.get("/api/training")
    def training_cards() -> dict[str, Any]:
        """Progreso del entrenamiento de aperturas (modo actual): {id de ejercicio: tarjeta}."""
        store, mode = training_store()
        return store.cards(mode)

    @app.post("/api/training")
    def training_record(req: TrainingResult) -> dict[str, Any]:
        if req.result not in ("right", "wrong"):
            raise HTTPException(422, "result tiene que ser right o wrong")
        store, mode = training_store()
        return store.record(mode, req.id, req.result)

    # --- Analizar posición (capturas de tableros digitales + Stockfish) ----------------------
    def vision_dir() -> Path:
        return load_config().data_path / "vision_profiles"

    @app.get("/api/vision/profiles")
    def vision_profiles() -> list[str]:
        return [p.name for p in vision.list_profiles(vision_dir())]

    @app.post("/api/vision/recognize")
    def vision_recognize(req: ImageRequest) -> dict[str, Any]:
        try:
            return vision.recognize(vision.load_image(req.image), vision.list_profiles(vision_dir()), req.white_bottom)
        except (ValueError, OSError) as e:
            raise HTTPException(422, f"No pude leer el tablero: {e}") from e

    @app.post("/api/vision/calibrate")
    def vision_calibrate(req: ImageRequest) -> dict[str, Any]:
        name = (req.name or "").strip()
        if not name:
            raise HTTPException(422, "Poné un nombre para el perfil (p.ej. chess.com)")
        try:
            profile, _box, white_bottom = vision.calibrate(vision.load_image(req.image), name)
        except (ValueError, OSError) as e:
            raise HTTPException(422, f"No pude calibrar: {e}") from e
        folder = vision_dir()
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{report_key(normalize_player(name))}.json").write_text(json.dumps(profile.to_json()), encoding="utf-8")
        return {"name": name, "white_bottom": white_bottom}

    @app.post("/api/position/analyze")
    def position_analyze(req: PositionRequest) -> dict[str, Any]:
        try:
            return position.analyse(load_config(), req.fen, multipv=max(1, min(req.multipv, 5)))
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        except RuntimeError as e:
            raise HTTPException(503, str(e)) from e

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
