# Radiografía de decisiones — sesión post-reinicio 2026-07-05

**Período:** 15:33 local (19:33 UTC) 5-jul → ~01:30 UTC 6-jul, 26 velas de 15m por bot.
**Método:** cada decisión de agente reanalizada contra el precio REAL posterior
(klines de Binance), simulando cada trade vetado con su SL/TP hasta tocar
stop/target o 12 velas. Herramienta: `scripts/analyze_decisions.py` (reutilizable).
Informes por símbolo: `logs/xray_ethusdc.md`, `logs/xray_solusdt.md`.

> Nota de honestidad: mi lectura "en vivo" durante la sesión fue demasiado
> optimista. Con el futuro real en la mano, el balance es mixto y hay un hallazgo
> que **contradice** esa lectura (el trend gate). Esto es justo lo que el análisis
> post-hoc debe destapar.

---

## 1. Veredicto de los vetos (¿útil = evitó pérdida, o costoso = evitó ganancia?)

Suma del PnL simulado que cada veto bloqueó (% sobre entrada). "Neto de vetos"
positivo = los filtros, en conjunto, nos ayudaron.

| Veto | ETH | SOL |
|---|---|---|
| **NANOFENIX** | 8 útiles / 2 costosos → **+2.84%** | 4 útiles / 2 costosos → **+1.04%** |
| **TREND_GATE** | 4 útiles / 3 costosos → **−0.06%** | 2 útiles / 6 costosos → **−1.23%** |
| **POST_STOPOUT** | — | 0 útiles / 2 costosos → **−2.20%** |
| **RESISTANCE** | 0 / 1 → −1.45% | 0 / 1 → −0.40% |
| **TECHNICAL_EXTENSION** | 0 / 1 → −0.42% | 1 / 0 → +0.91% |
| **NETO de todos los vetos** | **+0.91%** | **−1.88%** |

### Lecturas clave

- **NANOFENIX es el mejor filtro con diferencia.** En ambos símbolos bloqueó
  mayoritariamente trades perdedores (ETH +2.84%, SOL +1.04% netos). El hard-veto
  del companion por baja calidad de señal está funcionando como debe. Es el riel
  que más valor aportó.

- **TREND_GATE fue neto NEGATIVO, sobre todo en SOL (−1.23%).** Este es el
  hallazgo importante y va **contra** lo que celebré en vivo. Entre 17:00 y 19:30
  el companion marcó `TRENDING/BEAR` y el gate bloqueó **6 BUY consecutivos** por
  "BUY fades TRENDING/BEAR" — pero el precio de SOL SUBIÓ (81.03 → 81.75) todo ese
  tramo. Los 6 BUY habrían ganado. **La etiqueta `trend` del companion iba
  retrasada/invertida respecto al movimiento real**, y el gate la obedeció a
  ciegas. En un mercado lateral-alcista con "tendencia" mal etiquetada, el fade-block
  se convierte en un buy-block que cuesta dinero.

- **POST_STOPOUT (SOL) fue costoso (−2.20%)**: los 2 re-BUY que bloqueó tras el
  stopout habrían ganado. Es un filtro conservador por diseño (evita el peor caso
  de re-entrada en un sweep), pero en esta sesión el "sweep" no se materializó y el
  cooldown de 2 velas cortó rebotes válidos.

- **RESISTANCE / TECHNICAL_EXTENSION**: muestra pequeña (1 cada uno), señal débil.

---

## 2. Radiografía de los agentes (acierto direccional vs. vela siguiente)

| Agente | ETH | SOL | Lectura |
|---|---|---|---|
| **qabba** | 67% (9 calls) | 33% (3) | El mejor cuando se moja, pero casi siempre HOLD_QABBA (15-22 de 26 velas). Selectivo y acertado en ETH. |
| **decision** | 42% (19) | 47% (17) | Cerca de moneda al aire. Es el cuello de botella: sintetiza mal cuando los agentes discrepan. |
| **visual** | 39% (23) | 42% (24) | **Siempre da señal direccional (0 HOLD) y acierta <45%.** Es ruido con voz: empuja BUY casi siempre. |
| **technical** | 20% (5) | 50% (8) | Casi siempre HOLD; poca convicción. En ETH sus 5 apuestas fallaron 4. |
| **sentiment** | 0-50% (1-2) | idem | Casi siempre NEUTRAL. Aporta contexto, no dirección. |
| **risk** | n/a | n/a | Solo APPROVE/REDUCED, no direccional. |

### Por qué se equivocan (leído de sus reasonings)

1. **Visual está sesgado a BUY estructural.** Su razonamiento repite "price above
   EMA 9/21/50 and VWAP, SuperTrend bullish" en casi todas las velas — describe
   estructura de tendencia, no timing. Da BUY 24/26 veces y acierta 4 de 10. **Le
   falta**: microestructura y niveles de agotamiento (no sabe que el precio está
   estirado). Recomendación: bajarle peso o exigir confirmación de otro agente.

2. **QABBA es el más fiable pero se calla demasiado.** Cuando lee OBI/CVD extremos
   sí acierta (ej. ETH 16:15: "OBI 0.17, CVD negativo" → SELL correcto). Pero
   emite HOLD_QABBA en la mayoría por "divergencia" o "baja actividad". **Le falta**
   confianza para convertir lecturas de flujo claras en señal. Subir su peso cuando
   confidence ≥ 0.7.

3. **Decision no resuelve bien los conflictos.** En varias velas Technical=HOLD,
   Visual=BUY, QABBA=HOLD → Decision se va con Visual (BUY) y falla. El prompt
   dice "2 agentes de acuerdo → ejecuta", pero Visual+un neutral no es consenso
   real. **Le falta**: ponderar por track record (Visual acierta <45%, no debería
   arrastrar la decisión).

4. **El dato que le falta a TODOS: el régimen real, no el etiquetado.** El
   companion publica `trend=BULL/BEAR` pero esa etiqueta fue errónea en SOL 17:00-19:30.
   Nadie contrasta la etiqueta con el precio de las últimas velas.

---

## 3. Conclusiones accionables

**Prioridad alta:**

1. **Revisar el TREND_GATE (Fix #5).** Con datos reales fue neto negativo. Opciones:
   (a) exigir que la etiqueta `trend` del companion esté confirmada por el precio
   (ej. EMA-fast vs EMA-slow de las últimas N velas) antes de vetar; (b) solo vetar
   fades con `confidence` alta del companion Y `ema_trend_bps` de signo consistente;
   (c) subir el umbral para que solo bloquee fades claros, no todo BUY/SELL en
   régimen "TRENDING". **No desactivarlo** — evitó 6 trades perdedores en total —
   pero calibrarlo para no comerse los rebotes en lateral-alcista.

2. **Bajar el peso de Visual en Decision** o exigirle confirmación. Acierta <45%
   y siempre empuja BUY; hoy arrastró varias decisiones malas.

**Prioridad media:**

3. **Subir peso de QABBA cuando confidence ≥ 0.7** — es el agente más fiable pero
   demasiado callado.

4. **Reconsiderar POST_STOPOUT de 2 velas** en mercados sin sweep — costó 2 rebotes
   válidos en SOL. Quizá condicionarlo a que persista la presión adversa.

**Lo que SÍ funcionó y hay que conservar:**

- **NANOFENIX hard-veto**: el mejor filtro, neto claramente positivo en ambos.
- **Exit-on-blocked-entry (Fix #1)**: no aparece como "veto" porque no bloquea, pero
  cerró 3 posiciones heredadas limpiamente en vez de dejarlas atrapadas.
- **Sizing determinista (Fix #2)**: la única entrada nueva (ETH 18:45) recortó el
  notional del LLM de $529 (8.3×) a $95. Correcto por diseño.

**Balance global:** los fixes de seguridad (NanoFenix, sizing, exit, aislamiento)
son sólidos. El trend gate necesita calibración: en ESTA sesión (lateral-alcista
con tendencia mal etiquetada) restó valor, aunque conceptualmente ataca el patrón
correcto de la racha. La racha original fue en una V-reversal violenta; esta sesión
fue chop alcista — el mismo filtro se comporta distinto según el régimen, y eso es
justo lo que hay que afinar.
