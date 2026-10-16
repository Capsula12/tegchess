"""Cliente mínimo de la API de Lichess.

Reglas de la API que respetamos:
- Una request a la vez (el cliente es secuencial).
- Ante 429, esperar al menos un minuto antes de reintentar.
- User-Agent identificable: sin él, /api/games/user responde 404.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from datetime import date, datetime, timezone
from typing import Any

import httpx

from tegchess import __version__

log = logging.getLogger(__name__)

BASE_URL = "https://lichess.org"
EXPLORER_URL = "https://explorer.lichess.org"
USER_AGENT = f"tegchess/{__version__} (+https://github.com/Capsula12/tegchess)"

RATE_LIMIT_WAIT_S = 65
MAX_RETRIES = 4


class LichessError(RuntimeError):
    pass


def to_millis(d: date | datetime | None, end_of_day: bool = False) -> int | None:
    if d is None:
        return None
    if not isinstance(d, datetime):
        d = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        if end_of_day:
            d = d.replace(hour=23, minute=59, second=59, microsecond=999000)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return int(d.timestamp() * 1000)


class LichessClient:
    def __init__(self, token: str | None = None, timeout: float = 30.0):
        headers = {"User-Agent": USER_AGENT}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.has_token = bool(token)
        # read=None: los streams largos pueden tener pausas (el servidor limita partidas/segundo).
        self._http = httpx.Client(headers=headers, timeout=httpx.Timeout(timeout, read=None))

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> LichessClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def iter_user_games(
        self,
        username: str,
        *,
        since: int | None = None,
        until: int | None = None,
        max_games: int | None = None,
        perf_types: list[str] | None = None,
        rated: bool | None = None,
        color: str | None = None,
        ascending: bool = False,
    ) -> Iterator[dict[str, Any]]:
        """Recorre las partidas de un usuario como dicts JSON (por defecto, más recientes primero)."""
        params: dict[str, Any] = {
            "moves": "true",
            "tags": "true",
            "clocks": "true",
            "evals": "true",
            "opening": "true",
            "division": "true",
            "accuracy": "true",
            "finished": "true",
            "sort": "dateAsc" if ascending else "dateDesc",
        }
        if since is not None:
            params["since"] = since
        if until is not None:
            params["until"] = until
        if max_games:
            params["max"] = max_games
        if perf_types:
            params["perfType"] = ",".join(perf_types)
        if rated is not None:
            params["rated"] = str(rated).lower()
        if color:
            params["color"] = color

        url = f"{BASE_URL}/api/games/user/{username}"
        yield from self._stream_ndjson(url, params)

    def cloud_eval(self, fen: str, multi_pv: int = 1) -> dict[str, Any] | None:
        """Evaluación en la nube de Lichess, o None si la posición no está."""
        resp = self._request("GET", f"{BASE_URL}/api/cloud-eval", params={"fen": fen, "multiPv": multi_pv})
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    # --- internos -----------------------------------------------------

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self._http.request(method, url, **kwargs)
            except httpx.TransportError as e:
                wait = 5 * attempt
                log.warning("Error de red (%s). Reintento en %ss...", e, wait)
                time.sleep(wait)
                continue
            if resp.status_code == 429:
                log.warning("Lichess pidió esperar (429). Pausa de %ss...", RATE_LIMIT_WAIT_S)
                time.sleep(RATE_LIMIT_WAIT_S)
                continue
            if resp.status_code >= 500:
                wait = 10 * attempt
                log.warning("Error del servidor %s. Reintento en %ss...", resp.status_code, wait)
                time.sleep(wait)
                continue
            return resp
        raise LichessError(f"No se pudo completar {url} tras {MAX_RETRIES} intentos")

    def _stream_ndjson(self, url: str, params: dict[str, Any]) -> Iterator[dict[str, Any]]:
        headers = {"Accept": "application/x-ndjson"}
        yielded = 0
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                with self._http.stream("GET", url, params=params, headers=headers) as resp:
                    if resp.status_code == 429:
                        log.warning("Lichess pidió esperar (429). Pausa de %ss...", RATE_LIMIT_WAIT_S)
                        time.sleep(RATE_LIMIT_WAIT_S)
                        continue
                    if resp.status_code == 404:
                        raise LichessError(f"Usuario no encontrado o sin acceso: {url}")
                    if resp.status_code == 401:
                        raise LichessError("Token de Lichess inválido o vencido (revisá LICHESS_TOKEN)")
                    resp.raise_for_status()
                    for line in resp.iter_lines():
                        if line.strip():
                            yielded += 1
                            yield json.loads(line)
                    return
            except httpx.TransportError as e:
                # Si ya recibimos partidas no reintentamos acá: el llamador reanuda la descarga
                # incremental desde lo último guardado, sin duplicar.
                if yielded or attempt == MAX_RETRIES:
                    raise StreamInterrupted(f"Se cortó la descarga: {e}") from e
                wait = 5 * attempt
                log.warning("Error de red (%s). Reintento en %ss...", e, wait)
                time.sleep(wait)
        raise LichessError(f"No se pudo descargar {url}")


class StreamInterrupted(LichessError):
    pass
