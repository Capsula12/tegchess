# Estado del proyecto

Última actualización: 2026-10-17. Rama de trabajo: `feat/mvp` (publicada en GitHub: `Capsula12/tegchess`).

## Hecho (etapa 1, MVP)

- Descarga incremental desde Lichess (NDJSON con relojes, evaluaciones, aperturas y división de fases),
  reanudable ante cortes y con copia cruda en `data/raw/`.
- DuckDB con partidas, jugadas (posición antes/después, reloj, tiempo gastado), evaluaciones por
  posición y vistas de calidad de jugada.
- Motor: aprovecha evaluaciones de Lichess, nube de Lichess para aperturas repetidas y Stockfish en paralelo
  con caché por posición. Referencia: ~7.600 posiciones a profundidad 16 con 6 procesos ≈ 15 min.
- Informe por rival (JSON + interfaz + HTML estático + impresión a PDF):
  - Resumen: resultados por color y modalidad, cómo terminan sus partidas.
  - **Plan de partida** por color: escenarios de apertura, líneas, trampas, posiciones a evitar, qué reforzar.
  - **Trampas**: error habitual del rival, refutación (fácil/precisa, forzante) y cuántas veces lo castigaron.
  - Aperturas (familia/variante), explorador de árbol, posiciones flojas (del rival y mías).
  - **Expectativa por Elo** (yo vs. él en la modalidad común) y rendimiento sobre el Elo de cada uno, general,
    por color, por modalidad y forma reciente. Los "esperados" de aperturas y posiciones parten de esa base y suman
    la ventaja según el motor (peso 0,6) y el rendimiento sobre el Elo en la posición, suavizado con 30 partidas.
    Intervalos de 90% y chips de confianza / poca muestra. Metodología y backtest: `docs/PROPUESTA_ANALISIS.md`.
  - **Explorador**: árbol de repertorio de cada uno con, en cada posición, cuántas veces llegó el otro (con el color
    contrario): muestra dónde se cruzan los repertorios.
  - **Preparación**: su línea más jugada (hasta donde la probabilidad de que la siga es ≥ 25%), qué tan predecible
    es y líneas para memorizar ordenadas por probabilidad de llegar × ventaja (`analysis/prep.py`).
  - **Trampas** ordenadas por valor esperado: P(llega) × P(se equivoca) × ganancia × facilidad.
- **Mi perfil** (barra lateral → Yo; `data/reports/_me.json`): prioridades, mis métricas contra las de mis rivales
  (promedio, mejor del grupo y mi puesto; solo rivales con ≥ 20 partidas), mejores y peores aperturas, qué mejorar
  (reloj, aperturas, conversión, errores por fase y tipo, oportunidades perdidas, tilt/sesiones, finales) y mis
  posiciones flojas. Mis partidas se analizan completas (`engine.analyse_my_games_fully`, ~20 min la primera vez).
  - **Tiempo**: caídas por reloj, apuros (< 10% del tiempo inicial), reloj restante por jugada, tiempo por fase,
    pensadas largas y calidad de juego con poco reloj, comparado con el mío. Las ideas salen también en el plan.
- **Tablero**: cada cosa que se abre dice qué es ("Tu preparación", "Línea del rival", "Trampa para vos"...), mira
  desde el lado de quien juega esa línea (vos o el rival), con los nombres y colores arriba y abajo, y muestra la
  barra de evaluación de Stockfish con las evaluaciones ya guardadas (no corre un motor en el navegador).
- Interfaz local (`run.bat`): oscura, pizarra con acento dorado, pensada para entrar entera al 100% de zoom.
  Encabezado con los datos clave (expectativa por Elo, rating, partidas, rendimiento sobre el Elo, forma); barra
  lateral con mi perfil, los rivales (rating y mi expectativa contra cada uno, con scroll propio) y los botones de
  datos siempre a la vista; progreso de las tareas en una barra inferior.
- **Modo PGN** (Configuración → Origen de las partidas): usa los PGN de `data/pgn/` (p.ej. exportados de la
  Mega Database de ChessBase). La carpeta manda: archivos nuevos o cambiados se reimportan y los que se sacan se
  borran de la base. Buscador de jugadores en la barra lateral para elegir rivales; "yo" es mi nombre en los PGN.
  Fases calculadas por material (versión simple del criterio de Lichess), nombre de apertura de la lista oficial de
  Lichess (`src/tegchess/openings/`, CC0; coincide con Lichess en el 95% de las partidas de prueba), Elo de los
  tags, relojes y evaluaciones si los trae.
- **PDF**: botón PDF → este rival, todos o los elegidos; un archivo por rival en `informes/informe-<Rival>.pdf`
  (se reemplaza si existe). Lo genera Edge/Chrome headless imprimiendo la interfaz con `?print=<rival>`.

Preguntas del enunciado original y cobertura:

| # | Pregunta | Estado |
|---|---|---|
| 1 | Qué apertura mía conviene contra cada rival | Hecho: familias en común + posiciones compartidas con puntaje esperado |
| 2 | Variante más floja del rival | Hecho: posiciones repetidas ordenadas por daño total |
| 3 | Trampas que no conoce | Hecho: `analysis/traps.py` |
| 4 | Qué reforzar yo | Hecho: pestaña "Mi juego" (`analysis/improvement.py`) + posiciones flojas que él alcanza |
| 5 | Tiempo, fases, tipos de error, duración, estilo, tendencias | Tiempo, forma reciente y rendimiento sobre Elo para el rival; fases, tipos de error y tendencias para mí. Falta: estilo, y fases/errores del rival (sus partidas solo se analizan hasta el ply 30) |

## Próximos pasos sugeridos

1. **Pregunta 5 (perfil completo)**, todo con datos que ya están en la base:
   - Rendimiento por fase: usar `games.div_middle`/`div_end` (Lichess) y `move_quality` para la pérdida
     por fase. Requiere analizar partidas completas (`engine.max_ply: 0`), que es caro: evaluar
     analizar solo las partidas recientes completas.
   - Tipos de error: táctico (el error cambia material en pocas jugadas de la variante del motor) vs. posicional.
   - Estilo: frecuencia de cambios de damas, enroques opuestos, gambitos (material entregado en la apertura).
   - Tendencias: últimas N partidas vs. el histórico (aperturas nuevas, cambios de rating).
2. **Opening explorer con token** (`explorer.lichess.org/lichess`): para cada trampa, qué tan seguido la
   castiga la gente de un rating parecido; y popularidad de las líneas recomendadas.
3. Recalibrar los pesos de `analysis/expectation.py` cuando haya más datos (el backtest está descripto en
   `docs/PROPUESTA_ANALISIS.md`; convendría pasarlo a un módulo `analysis/calibration.py` con su test).
4. Mejoras de interfaz: navegar variantes del motor en el tablero, filtros por modalidad o fecha en el informe.
5. **Estudios de Lichess**: exportar el plan, las trampas y las líneas como PGN con comentarios y flechas
   (`[%cal]`), un capítulo por línea, e importarlos con `POST /api/study/{id}/import-pgn` (token con
   `study:write`, hasta 64 capítulos por estudio).
6. Etapa 2 (no empezada): práctica con repetición espaciada sobre las posiciones de `repeated_positions`
   y desvíos de repertorio; actualización automática al detectar partidas nuevas.

## Para revisar

- Modo PGN: la modalidad sale de `TimeControl` o del nombre del torneo; la Mega casi nunca trae `TimeControl`, así que
  todo queda como "classical" salvo torneos con "blitz"/"rapid" en el nombre. La expectativa usa el Elo FIDE de los tags.

- La calidad de juego con poco reloj usa solo jugadas evaluadas; con `engine.max_ply: 30` casi no hay jugadas
  con apuro evaluadas (salvo partidas analizadas en Lichess). La interfaz lo avisa.
- Un 429 en la descarga es esperable tras varios pedidos seguidos: el cliente espera 65 s y sigue. Hoy solo se ve
  en la consola; mostrarlo en la barra de progreso.

- En la primera corrida real, algunos jugadores bajaron un poco más que `max_games` (540 y 510 con tope 500),
  en un único tramo continuo y sin duplicados. Revisar `ingest.sync_user` (cálculo de `remaining` tras una
  reanudación) con la base del usuario. No afecta los resultados: solo entran unas partidas más viejas de más.

## Decisiones tomadas

- DuckDB como única base, con la lógica de métricas en SQL (macros + vistas) para reutilizarla.
- Posiciones por EPD para que cuenten las transposiciones.
- Puntaje esperado del cruce: `0.4·mío + 0.4·(1 − suyo) + 0.2·motor`, cada puntaje suavizado con 4 tablas ficticias.
- Trampa = jugada habitual del rival con pérdida ≥ 8 puntos de % de victoria en una posición que repite ≥ 3 veces.
  "Precisa" si la 2ª mejor respuesta gana ≥ 10 puntos menos que la mejor.
- **Mínimos de muestra** (`analysis/expectation.py`): 3 partidas para una posición o jugada, 5 para una familia de
  aperturas, 20 para rasgos generales (reloj, rendimiento). Una jugada se marca "a corregir" solo si se repitió ≥ 3
  veces, pierde ≥ 5 puntos de % de victoria (imprecisión de Lichess) y el motor prefiere otra (`worth_correcting`).
- **Preparación**: la ventaja se mide contra lo normal para mi color (la evaluación de la posición inicial), no contra
  0,00; si no, con negras nunca aparecía nada.
- **Sin IA**: todo el análisis es determinístico (python-chess, DuckDB, Stockfish). El token de Lichess solo se usa
  para descargar partidas (y a futuro para el opening explorer). Si alguna vez se suma IA, que sea opcional y aparte.
- PDF con la impresión del navegador (sin WeasyPrint, que en Windows necesita GTK): Edge headless desde el servidor.
  `msedge.exe` delega y vuelve enseguida, por eso `pdf.py` espera a que el archivo deje de crecer.
- Sin build de front: JS vanilla y chessground vendorizado (GPL-3.0; tenerlo en cuenta si el repo se publica con otra licencia).
