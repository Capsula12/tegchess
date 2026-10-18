"""PDF de los informes con Edge/Chrome en modo headless (imprime la interfaz en modo impresión).

Se usa el navegador porque ya está instalado en Windows y respeta el CSS de impresión de web/;
WeasyPrint necesitaría GTK.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

import json

from tegchess.config import PROJECT_ROOT, Config, normalize_player

log = logging.getLogger(__name__)

PDF_DIR = PROJECT_ROOT / "informes"
# Tiempo virtual que el navegador espera a que la página cargue los datos y se dibuje.
RENDER_BUDGET_MS = 20000
TIMEOUT_S = 120


def find_browser() -> str | None:
    """Edge (siempre está en Windows) o Chrome/Chromium."""
    for name in ("msedge", "chrome", "google-chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    candidates = []
    for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            candidates += [Path(base) / "Microsoft/Edge/Application/msedge.exe",
                           Path(base) / "Google/Chrome/Application/chrome.exe"]
    return next((str(p) for p in candidates if p.exists()), None)


def _pdf_name(name: str) -> str:
    return "informe-" + ("".join(c for c in name if c.isalnum() or c in "-_") or "rival") + ".pdf"


def pdf_filename(cfg: Config, rival: str) -> str:
    """informe-<Nombre>.pdf con el nombre como figura en la configuración ("Perez, Juan" -> PerezJuan)."""
    return _pdf_name(cfg.display_name(normalize_player(rival)))


def export_pdfs(cfg: Config, rivals: list[str], base_url: str,
                progress: Callable[[str, int | None], Callable[[], None]],
                out_dir: Path = PDF_DIR) -> dict[str, dict[str, object]]:
    """Un PDF por rival en `out_dir`, reemplazando el anterior. Devuelve {rival: {ok, path | error}}."""
    browser = find_browser()
    if not browser:
        raise RuntimeError("No encontré Edge ni Chrome para generar el PDF. Usá 'Imprimir este' en el diálogo.")
    out_dir.mkdir(parents=True, exist_ok=True)
    tick = progress("Generando PDF", len(rivals))
    result: dict[str, dict[str, object]] = {}
    # Perfiles temporales: así no choca con un Edge/Chrome que el usuario tenga abierto.
    with tempfile.TemporaryDirectory(prefix="tegchess_pdf_", ignore_cleanup_errors=True) as profiles:
        for rival in rivals:
            report_file = cfg.reports_path / f"{rival}.json"
            if not report_file.exists():
                result[rival] = {"ok": False, "error": "no hay informe generado"}
                tick()
                continue
            meta = json.loads(report_file.read_text(encoding="utf-8")).get("meta", {})
            target = out_dir / _pdf_name(meta.get("rival_name") or rival)
            tmp = target.with_name(target.stem + ".tmp.pdf")
            tmp.unlink(missing_ok=True)
            profile = Path(profiles) / rival
            url = f"{base_url.rstrip('/')}/?print={quote(rival)}"
            cmd = [browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                   "--disable-extensions", f"--user-data-dir={profile}", "--no-pdf-header-footer",
                   f"--virtual-time-budget={RENDER_BUDGET_MS}", f"--print-to-pdf={tmp}", url]
            try:
                subprocess.run(cmd, capture_output=True, timeout=TIMEOUT_S, check=False)
                _wait_for_file(tmp, TIMEOUT_S)
                try:
                    os.replace(tmp, target)
                except PermissionError as e:
                    raise RuntimeError("el PDF anterior está abierto en otro programa; cerralo y probá de nuevo") from e
                result[rival] = {"ok": True, "path": str(target)}
            except (RuntimeError, subprocess.TimeoutExpired, OSError) as e:
                log.error("PDF de %s: %s", rival, e)
                result[rival] = {"ok": False, "error": str(e) if not isinstance(e, subprocess.TimeoutExpired)
                                 else "el navegador tardó demasiado"}
                tmp.unlink(missing_ok=True)
            tick()
    return result


def _wait_for_file(path: Path, timeout_s: float) -> None:
    """msedge.exe delega en otro proceso y vuelve enseguida: esperar a que el PDF exista y deje de crecer."""
    deadline = time.monotonic() + timeout_s
    last = -1
    while time.monotonic() < deadline:
        size = path.stat().st_size if path.exists() else -1
        if size > 0 and size == last:
            return
        last = size
        time.sleep(0.5)
    raise RuntimeError("el navegador no generó el archivo")
