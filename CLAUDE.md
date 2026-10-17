# CLAUDE.md — guía para sesiones de Claude Code en este repo

Leé también [docs/ESTADO.md](docs/ESTADO.md): estado actual, decisiones y próximos pasos.

## Qué es

Herramienta de preparación de ajedrez con la API de Lichess + Stockfish (Python 3.11+, Windows).
Etapa 1 (hecha en su MVP): informe por rival con cruce de repertorios y trampas.
Etapa 2 (futura): web app de práctica de repertorio con repetición espaciada. Por eso el núcleo
(`src/tegchess`) no depende de la presentación (`web/`) y la salida es JSON + DuckDB reutilizables.

## Cómo trabajar (preferencias del usuario)

- Docs, mensajes de commit y textos de la interfaz **en español** (rioplatense: "vos"); nombres de código en inglés.
- Commits chicos y descriptivos, en una rama (no directo a `main`). **No hacer push sin que lo pida.**
- Ante algo ambiguo que cambie el diseño: preguntar antes. Detalles menores: decidir y contarlo.
- Datos, token y config fuera del repo (`data/`, `.env`, `config.yaml` e `informes/*.pdf` están en `.gitignore`).

## Comandos

```bat
run.bat                                 :: entorno + dependencias + interfaz (http://127.0.0.1:8765)
.venv\Scripts\python -m pytest          :: tests (~10 s; los de motor se saltean sin Stockfish)
.venv\Scripts\python -m tegchess doctor :: qué detectó: usuarios, Stockfish, token
.venv\Scripts\python -m tegchess fetch | analyze | report | serve | openings USER
```

Para probar sin tocar los datos del usuario: apuntar `TEGCHESS_CONFIG` a un yaml propio con
`data_dir` en otra carpeta (p.ej. el scratchpad). El usuario usa `config.yaml` + `data/`.

## Arquitectura (src/tegchess)

| Módulo | Rol |
|---|---|
| `config.py` | `config.yaml` (pydantic), `.env` → `LICHESS_TOKEN`, detección de Stockfish (PATH o winget) |
| `lichess.py` | cliente HTTP secuencial: NDJSON en streaming, 429 → espera 65 s, reintentos |
| `ingest.py` | parseo de partidas (python-chess) y sincronización incremental (`sync_state`) |
| `db.py` | esquema DuckDB, macros `eval_cp`/`win_pct`, vistas `player_games` y `move_quality` |
| `engine.py` | evaluaciones pendientes por EPD: terminales → nube de Lichess → pool de Stockfish |
| `metrics.py` | equivalente Python de las macros (CPL, % de victoria, umbrales de Lichess) |
| `analysis/scope.py` | CTE con el alcance de partidas de un jugador (modalidad, fechas, max_games) |
| `analysis/openings.py` | resumen de aperturas, árbol de repertorio, posiciones repetidas |
| `analysis/expectation.py` | expectativa por Elo + ajustes (motor 0,6, residuo suavizado K=30), intervalos, forma |
| `analysis/crossover.py` | cruce yo vs. rival: familias, posiciones compartidas, puntaje esperado sobre la base de Elo |
| `analysis/prep.py` | preparación: línea principal del rival, predictibilidad, líneas para memorizar |
| `analysis/improvement.py` | "Mi juego": áreas a mejorar ordenadas por puntos cada 100 partidas |
| `analysis/traps.py` | trampas: error habitual + refutación (multipv 3), ordenadas por valor esperado |
| `analysis/clock.py` | manejo del tiempo: caídas por reloj, apuros, tiempo por fase, calidad con poco reloj |
| `analysis/lines.py` | línea para llegar a una posición, SAN, variantes numeradas |
| `report.py` | arma el JSON del informe (+ plan de partida) y exporta HTML estático |
| `pdf.py` | PDF por rival en `informes/` con Edge/Chrome headless (`?print=<rival>`) |
| `pipeline.py` | pasos compartidos por CLI y servidor |
| `server.py` | FastAPI: informes, config, tareas largas con progreso (una a la vez) |
| `web/` | HTML + JS vanilla sin build; chessground convertido a script clásico |

## Convenciones y trampas conocidas

- **Posiciones**: clave = EPD (`board.epd()`, FEN sin contadores). Evaluaciones siempre **desde blancas**;
  `eval_cp` acota a ±1000 y el mate cuenta como ±1000. Pérdida de % de victoria con la fórmula de Lichess;
  umbrales 5/10/15 = imprecisión/error/error grave.
- **Lichess**: sin `User-Agent` propio, `/api/games/user` responde **404**. El opening explorer
  (`explorer.lichess.org`) exige token (401 sin él); cloud-eval no.
- **Cloud-eval** es para "unas pocas posiciones": miles de pedidos seguidos terminan en 429. Por eso se piden
  solo las posiciones más repetidas, con tope y pausa (`engine.cloud_eval_*`), y ante un 429 se corta y
  sigue Stockfish (`RateLimited`). Las descargas de partidas sí esperan 65 s ante un 429.
- En el JSON de partidas, `clocks[i]` es el reloj de quien movió tras el ply `i+1`, y `analysis[i]` es la
  evaluación tras el ply `i+1`. Las evaluaciones de Lichess no traen mejor jugada salvo en errores:
  `engine.ensure_best_moves` la completa para las posiciones que muestra el informe.
- **DuckDB**: un solo proceso escritor y, en Windows, el archivo queda bloqueado mientras está abierto
  (ni siquiera se puede abrir en solo lectura desde otro proceso). El servidor abre la base solo dentro
  de las tareas; los informes se sirven desde los JSON. No correr `tegchess analyze` mientras la interfaz
  está procesando.
- Columnas JSON (`evals.lines`): pasar objetos Python a `db.bulk_insert`, no strings ya serializados
  (si no, quedan doblemente codificados; `traps._lines` tolera filas viejas así).
- `web/` tiene que funcionar con `file://` (exportación estática): nada de módulos ES.
  `web/vendor/chessground.global.js` es chessground 9.2.1 (GPL-3.0) envuelto en una IIFE que expone `window.Chessground`.
- Consola de Windows: la CLI fuerza UTF-8 en stdout/stderr (rich con caracteres de progreso).
- `run.bat`: el separador de `::` protege una instancia que esté corriendo mientras se reescribe el
  archivo (cmd retoma la lectura por posición de byte). Se puede sacar cuando ya no haga falta.
