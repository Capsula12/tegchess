# Estado del proyecto

Última actualización: 2026-10-16. Rama de trabajo: `feat/mvp` (sin push a GitHub todavía).

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
- Interfaz local (`run.bat`): editar config, botones Descargar / Analizar / Generar informes / Todo.

Preguntas del enunciado original y cobertura:

| # | Pregunta | Estado |
|---|---|---|
| 1 | Qué apertura mía conviene contra cada rival | Hecho: familias en común + posiciones compartidas con puntaje esperado |
| 2 | Variante más floja del rival | Hecho: posiciones repetidas ordenadas por daño total |
| 3 | Trampas que no conoce | Hecho: `analysis/traps.py` |
| 4 | Qué reforzar yo | Hecho (básico): mis posiciones flojas que él alcanza + familias donde mi CPL > el suyo |
| 5 | Tiempo, fases, tipos de error, duración, estilo, tendencias | **Parcial**: solo duración media y forma de terminar |

## Próximos pasos sugeridos

1. **Pregunta 5 (perfil completo)**, todo con datos que ya están en la base:
   - Manejo del tiempo: caídas por reloj (ya está `status = outoftime`), tiempo medio por jugada según fase,
     jugadas donde más piensa, apuros (reloj < 10% con muchas jugadas por delante).
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
5. Etapa 2 (no empezada): práctica con repetición espaciada sobre las posiciones de `repeated_positions`
   y desvíos de repertorio; actualización automática al detectar partidas nuevas.

## Para revisar

- En la primera corrida real, algunos jugadores bajaron un poco más que `max_games` (540 y 510 con tope 500),
  en un único tramo continuo y sin duplicados. Revisar `ingest.sync_user` (cálculo de `remaining` tras una
  reanudación) con la base del usuario. No afecta los resultados: solo entran unas partidas más viejas de más.

## Decisiones tomadas

- DuckDB como única base, con la lógica de métricas en SQL (macros + vistas) para reutilizarla.
- Posiciones por EPD para que cuenten las transposiciones.
- Puntaje esperado del cruce: `0.4·mío + 0.4·(1 − suyo) + 0.2·motor`, cada puntaje suavizado con 4 tablas ficticias.
- Trampa = jugada habitual del rival con pérdida ≥ 8 puntos de % de victoria en una posición que repite ≥ 3 veces.
  "Precisa" si la 2ª mejor respuesta gana ≥ 10 puntos menos que la mejor.
- PDF con la impresión del navegador (sin WeasyPrint, que en Windows necesita GTK).
- Sin build de front: JS vanilla y chessground vendorizado (GPL-3.0; tenerlo en cuenta si el repo se publica con otra licencia).
