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
  - **Tiempo**: caídas por reloj, apuros (< 10% del tiempo inicial), reloj restante por jugada, tiempo por fase,
    pensadas largas y calidad de juego con poco reloj, comparado con el mío. Las ideas salen también en el plan.
- Interfaz local (`run.bat`), oscura estilo reproductor de música: rivales en la barra lateral, Descargar / Analizar /
  Generar informes / Todo, progreso en una barra inferior.
- **PDF**: botón PDF → este rival, todos o los elegidos; un archivo por rival en `informes/informe-<Rival>.pdf`
  (se reemplaza si existe). Lo genera Edge/Chrome headless imprimiendo la interfaz con `?print=<rival>`.

Preguntas del enunciado original y cobertura:

| # | Pregunta | Estado |
|---|---|---|
| 1 | Qué apertura mía conviene contra cada rival | Hecho: familias en común + posiciones compartidas con puntaje esperado |
| 2 | Variante más floja del rival | Hecho: posiciones repetidas ordenadas por daño total |
| 3 | Trampas que no conoce | Hecho: `analysis/traps.py` |
| 4 | Qué reforzar yo | Hecho (básico): mis posiciones flojas que él alcanza + familias donde mi CPL > el suyo |
| 5 | Tiempo, fases, tipos de error, duración, estilo, tendencias | **Parcial**: tiempo hecho (`analysis/clock.py`); faltan fases, tipos de error, estilo y tendencias |

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
3. Calidad de recomendaciones: con pocas partidas en común el plan es ruidoso. Ideas: agrupar posiciones
   por familia de apertura, mostrar intervalos de confianza, priorizar posiciones que el rival repite.
4. Mejoras de interfaz: navegar variantes del motor en el tablero, filtros por modalidad o fecha en el informe.
5. **Estudios de Lichess**: exportar el plan, las trampas y las líneas como PGN con comentarios y flechas
   (`[%cal]`), un capítulo por línea, e importarlos con `POST /api/study/{id}/import-pgn` (token con
   `study:write`, hasta 64 capítulos por estudio).
6. Etapa 2 (no empezada): práctica con repetición espaciada sobre las posiciones de `repeated_positions`
   y desvíos de repertorio; actualización automática al detectar partidas nuevas.

## Para revisar

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
- PDF con la impresión del navegador (sin WeasyPrint, que en Windows necesita GTK): Edge headless desde el servidor.
  `msedge.exe` delega y vuelve enseguida, por eso `pdf.py` espera a que el archivo deje de crecer.
- Sin build de front: JS vanilla y chessground vendorizado (GPL-3.0; tenerlo en cuenta si el repo se publica con otra licencia).
