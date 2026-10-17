# Propuesta: cómo construir el análisis y las expectativas

Fecha: 2026-10-17. Estado: **implementada** (A, B, C, D y E; ver docs/ESTADO.md). El usuario pidió no perder de
vista la preparación específica contra un rival: por eso se sumó la pestaña "Preparación" (analysis/prep.py).

## Qué medimos (backtest con los datos reales)

Para los 10 jugadores con ≥ 40 partidas (yo + rivales) se tomó el 70% más viejo de sus partidas como historia
y el 30% más nuevo como prueba (975 partidas). En cada partida de prueba se predijo el resultado con distintos
modelos y se midió el error (Brier: 0 = perfecto, 0,25 = tirar una moneda). Script: se puede rehacer con los
datos de `data/` (ver "Cómo reproducir" abajo).

| Modelo | Brier |
|---|---|
| Solo el puntaje bruto de la familia de apertura (lo que más pesa hoy) | 0,2350 (el peor) |
| Solo Elo | 0,2271 |
| Elo + puntaje bruto de la familia | 0,2270 |
| Elo + desvío por familia (resultado − esperado por Elo), suavizado con 4 partidas | 0,2308 (peor que Elo solo) |
| Elo + desvío por familia, suavizado con 50 partidas | 0,2257 |
| Elo + evaluación media con que el jugador **sale** de esa apertura (historia) | 0,2257 |
| Elo + evaluación real al ply 20 de esa partida (cota: si supiéramos cómo sale de la apertura) | 0,2195 |

Además, por color y familia (69 casos con muestra):

- La evaluación con que un jugador sale de una apertura es **estable**: correlación 0,67 entre historia y prueba.
- El resultado por familia (ya corregido por Elo) **casi no se repite**: correlación 0,12. Es mayormente ruido.

## Conclusiones

1. **El Elo es la base.** Lichess empareja ratings parecidos (entre −126 y +59 en el 80% de las partidas), por eso
   dentro de las partidas de cada uno el Elo varía poco. Pero entre vos y un rival concreto la diferencia puede ser
   grande (Maxivo ~2180 vs. vos ~1800 → esperado ~10%). Hoy el "esperado" la ignora y puede decir 58% contra alguien
   400 puntos más fuerte.
2. **Los porcentajes por apertura engañan con muestras chicas.** Hay que corregirlos por el Elo de los rivales de
   cada partida (puntaje *por encima de lo esperado*, no puntaje bruto) y suavizarlos mucho más (≈ 20-50 partidas
   ficticias, no 4). Mostrarlos con intervalo y marcar "poca muestra".
3. **La señal más confiable es el motor: cómo sale cada uno de la apertura.** Es estable y predice. Las
   recomendaciones de apertura deberían basarse sobre todo en "dónde quedás mejor parado vos y peor parado él",
   y usar los resultados como apoyo.
4. **Lo que más mueve el resultado está después de la apertura** (la cota de 0,2195 lo muestra) y, en tu caso,
   el reloj: el 37% de tus derrotas son por tiempo. La pestaña propia tiene mucho para ganar ahí.

## Propuesta de cambios

### A. Expectativa contra el rival (cabecera del informe y plan)

- **Expectativa base por Elo**: tu rating vs. el suyo en la modalidad elegida (último rating y tendencia),
  en % y como "de cada 10 partidas, ~X puntos". Si la diferencia supera ~200, avisar que el plan apunta a
  *mejorar las chances* más que a ganar.
- **Rendimiento por encima del Elo** (el suyo y el tuyo): puntaje real vs. esperado por los ratings de cada
  partida, en general, por color y por modalidad. Dice si el rival rinde más o menos que su rating.
- **Forma reciente**: últimas 30 partidas vs. el resto (rating, rendimiento sobre Elo, aperturas nuevas).

### B. Nuevo puntaje de las aperturas y posiciones (reemplaza el 40/40/20)

Todo expresado como **cambio sobre la expectativa base** (puntos de %), así se lee igual contra un rival
más fuerte o más débil:

```
esperado = base_Elo + Δ
Δ = w_motor · (prob. de victoria por el motor en la posición − 50%)
  + w_res   · desvío_suavizado(mío)  +  w_res · (−desvío_suavizado(suyo))
desvío_suavizado = Σ(resultado − esperado por Elo) / (n + K),  K ≈ 30
```

con pesos calibrados en el backtest (hoy indican motor ≫ resultados). Para las familias: "cómo salgo yo vs.
cómo sale él" (evaluación media al ply 20, estable) en lugar del puntaje bruto. Cada número con su `n` y un
indicador de confianza (alta / media / baja).

### C. Trampas: probabilidad, no solo frecuencia

- Probabilidad de que la trampa *aparezca*: P(llegamos a la posición) × P(él juega el error). La primera sale del
  cruce (frecuencia con la que él llega jugando el color contrario y si vos podés forzar el camino).
- Valor esperado = probabilidad × ganancia (puntos de % de victoria) × facilidad de la refutación.
- Ordenar por valor esperado, no por veces × pérdida.

### D. Pestaña nueva "Mi juego" (aspectos a mejorar en general)

Ordenada por **puntos que te cuesta cada problema cada 100 partidas** (estimado), con 3-5 prioridades arriba:

1. **Reloj**: derrotas por tiempo con posición ganada o pareja, apuros, fases donde gastás de más (ya medido).
2. **Aperturas**: familias donde salís peor según el motor (señal estable), posiciones repetidas donde perdés
   (ya existe), aperturas que conviene abandonar o estudiar.
3. **Rendimiento por fase**: errores en medio juego vs. final. Requiere analizar tus partidas completas
   (~18.000 posiciones más, ≈ 20 min con Stockfish); los rivales siguen solo con la apertura.
4. **Tipos de error**: táctico (la mejor jugada era captura, jaque o ganaba material en la variante) vs.
   posicional; **oportunidades perdidas** (el rival se equivocó y no lo castigaste).
5. **Conversión y defensa**: % de posiciones ganadas (+3) que ganás y de perdidas que salvás.
6. **Tendencias**: después de perder (tilt), rachas, por modalidad, por color, evolución del rating.
7. **Finales**: resultado por tipo de final (torres, alfiles, peones…) a partir de la división de Lichess.

### E. Presentación de la incertidumbre (en todo el informe)

- Intervalos (Wilson) en los porcentajes y un chip "poca muestra" cuando `n` < 10.
- Evitar frases categóricas cuando la evidencia es débil ("probable", "indicio").
- Una sección "Cómo leer este informe" con la explicación de la expectativa.

## Orden sugerido

1. A + B + E (expectativa y puntajes nuevos; cambia el cruce y el plan). Incluye un backtest automatizado en los
   tests para que los pesos queden justificados.
2. D en dos partes: primero lo que ya está en la base (reloj, aperturas, tendencias, conversión con lo evaluado),
   después el análisis completo de tus partidas (fases, tipos de error, oportunidades perdidas, finales).
3. C (trampas por valor esperado).

## Cómo reproducir el backtest

Partidas `blitz`/`rapid` rated de los jugadores con ≥ 40 partidas, ordenadas por fecha, corte 70/30 por jugador.
Esperado por Elo: `1 / (1 + 10^((Elo_rival − Elo_propio) / 400))`. Probabilidad del motor: fórmula de Lichess
(`win_pct`). La idea es mover el script a `src/tegchess/analysis/calibration.py` en el paso 1.
