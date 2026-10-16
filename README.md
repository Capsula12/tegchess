# tegchess

Herramienta de preparación de ajedrez basada en la API de Lichess y Stockfish.

**Etapa 1 (actual):** preparar rivales concretos de un torneo. Descarga tus partidas y las de tus
rivales, las guarda en DuckDB, las analiza con Stockfish y genera un informe por rival
(aperturas, variantes flojas, trampas, qué reforzar, perfil) navegable en HTML con tablero interactivo.

**Etapa 2 (futura):** web app de práctica de repertorio con repetición espaciada. Por eso el núcleo
de análisis (`src/tegchess`) está separado de la presentación (`web/`) y los informes se generan como JSON.

## Requisitos

- Windows, Python 3.11+ (recomendado 3.13).
- Stockfish: `winget install Stockfish.Stockfish` (se detecta solo) o indicar la ruta en `config.yaml`.

## Uso rápido

```bat
run.bat
```

Crea el entorno `.venv`, instala dependencias, copia `config.example.yaml` a `config.yaml` si no existe
y abre la interfaz en el navegador.

Desde la línea de comandos (con el entorno activado: `.venv\Scripts\activate`):

```bat
tegchess doctor            :: muestra qué detectó (Stockfish, token, usuarios)
tegchess fetch             :: descarga partidas (incremental) de vos y los rivales
tegchess analyze           :: evalúa posiciones (Lichess, nube de Lichess, Stockfish)
tegchess report            :: genera los informes JSON + HTML estático en data/reports
tegchess serve             :: levanta la interfaz web
```

Desde la interfaz (`run.bat`) se puede editar la configuración y lanzar los mismos pasos con los
botones **Descargar**, **Analizar**, **Generar informes** o **Todo**. El botón **PDF** imprime el
informe (elegí "Guardar como PDF"). `tegchess report` también deja una versión estática en
`data/export/index.html` que se abre con doble clic, sin servidor.

### Cuánto tarda el análisis

Cada posición se evalúa una sola vez (caché por posición) y se aprovechan las evaluaciones que ya trae
Lichess. Como referencia, ~7.600 posiciones nuevas a profundidad 16 tardaron ~15 minutos con 6 procesos.
Para ir más rápido: bajar `engine.depth` (14 alcanza para aperturas de blitz), subir `engine.workers` o
reducir `engine.max_ply`. El análisis se puede cortar con Ctrl+C y retomar: lo evaluado queda guardado.

## Token de Lichess (opcional)

Un token personal (https://lichess.org/account/oauth/token, **sin permisos especiales**) acelera la
descarga (30 partidas/s en vez de 20) y habilita el opening explorer, que hoy exige autenticación.
Guardalo en un archivo `.env` en la raíz del repo (está en `.gitignore`):

```
LICHESS_TOKEN=lip_xxxxxxxxxxxx
```

## Estructura

```
src/tegchess/   núcleo: cliente Lichess, ingesta, DuckDB, motor, métricas, análisis, informe, servidor
web/            interfaz (HTML + JS vanilla, chessground y chess.js en web/vendor)
data/           base DuckDB e informes generados (ignorado por git)
tests/          tests con partidas de ejemplo
```

## Tests

```bat
.venv\Scripts\python -m pytest
```
