# Post-mortem: racha de pérdidas 2026-07-05 y mejoras derivadas

**Fecha del incidente:** 2026-07-05, 05:30–09:30 UTC
**Impacto:** −14.7 USDT en 5 trades perdedores consecutivos (ETH USDC + SOL USDT)
**Estado:** Mitigaciones implementadas y desplegadas

---

## 1. Qué ocurrió

Durante una reversión en V (V-reversal) del mercado, ambos bots encadenaron pérdidas:

- **ETH 05:48–05:49**: la posición LONG fue barrida por el stop-loss (liquidity sweep) y **30 segundos después** el bot abrió un SHORT persiguiendo el mismo movimiento que acababa de barrer su stop. El mercado revirtió (era una capitulación) y el SHORT también perdió.
- **QABBA** malinterpretó la capitulación: leyó OBI 0.12 + CVD −2001 (extremos de pánico vendedor) como *continuación* bajista, cuando la literatura de order flow (VSA, volume climax, absorción) indica que los extremos de flujo con RSI <30 son señales de **reversión**.
- **Technical** acertó (BUY 0.65 por RSI 29.5 sobreventa) y **NanoFenix** también decía LONG, pero Decision se fue con QABBA (SELL 0.85) + Visual (SELL 0.70).
- **SOL 09:30**: ejecutó un SELL con solo 1/3 de consenso estricto.
- El modo **CAUTION** se activó correctamente tras 3 pérdidas, pero su cooldown de **300s** era inútil en timeframe de 15m (expiraba antes de la siguiente vela).
- El **veto de NanoFenix existía pero estaba desarmado** (`FENIX_NANOFENIX_REQUIRE_ALLOW_EXECUTE` por defecto en False).

## 2. Causas raíz

| # | Causa | Tipo |
|---|---|---|
| 1 | Re-entrada inmediata en la dirección del barrido tras stopout | Falta de filtro |
| 2 | QABBA sin concepto de capitulación/clímax en su prompt | Prompt incompleto |
| 3 | Cooldown CAUTION (300s) no escalado al timeframe | Config inadecuada |
| 4 | Veto NanoFenix desarmado por defecto | Config |

## 3. Mejoras implementadas (con base en literatura)

### 3.1 Filtro post-stopout direccional (`src/trading/engine.py`)
Tras un cierre con pérdida, se bloquean durante N velas (default 2, env
`FENIX_POST_STOPOUT_BLOCK_BARS`, 0 desactiva) las **nuevas entradas en la
dirección del movimiento que barrió el stop**:
- LONG perdedor → se bloquean SELL (el movimiento fue bajista)
- SHORT perdedor → se bloquean BUY

El bloqueo se registra en `_close_position_record` y se aplica como filtro
`POST_STOPOUT` en el pipeline de decisión (antes del gate LLM-risk). Habría
prevenido directamente el SHORT de ETH a las 05:49.

*Base:* regla anti stop-hunt ("nunca perseguir el sweep") + protocolos de
re-entrada post-stopout.

### 3.2 Cooldown CAUTION 300s → 1800s (`src/risk/runtime_feedback.py`)
- `caution_cooldown_seconds`: 300 → **1800** (2 velas de 15m)
- `severe_cooldown_seconds`: 900 → **3600**
- Ambos ahora sobrescribibles por env: `FENIX_CAUTION_COOLDOWN_SECONDS`,
  `FENIX_SEVERE_COOLDOWN_SECONDS`.

*Base:* protocolos de cooldown escalonado tras pérdidas consecutivas,
escalados al timeframe operativo.

### 3.3 Regla de capitulación en QABBA (`src/prompts/agent_prompts.py`)
Nuevo bloque `CAPITULATION / CLIMAX OVERRIDE` en el system prompt:
OBI extremo (<0.2 o >5.0) + CVD extremo en la misma dirección + RSI agotado
(<30 / >70) = probable clímax de capitulación o liquidity sweep → **HOLD_QABBA**
(o señal contra el flujo si hay absorción clara), nunca extrapolación de
continuación ni señal de alta confianza persiguiendo el barrido.

*Base:* VSA / volume climax (volumen ≥3× media = capitulación → reversión),
distinción absorción vs. agotamiento (order flow).

### 3.4 Veto NanoFenix armado (config, ya desplegado 2026-07-05)
`FENIX_NANOFENIX_REQUIRE_ALLOW_EXECUTE=1` en `.env` de ambos bots.

## 4. Pendientes (evaluar con más datos)

- **Gate ADX contra-tendencia** (caso invertido: no hacer fade de tendencias
  con ADX >25-30). Hoy ADX es solo contexto de prompt.
- **Pesos por régimen**: NanoFenix ya publica `Regime=TRENDING/DEAD/VOLATILE`
  y `Trend=BULL/BEAR` sin que nadie consuma esos campos para ponderar agentes.
- **Consenso mínimo 2/3 para ejecutar** (SOL ejecutó con 1/3).
- **A/B MTF**: el bias 1h en SOL vetó 3 BUY de recuperación que habrían ganado
  — el MTF restó valor en la reversión. Seguir midiendo.

## 5. Tests

`tests/test_post_stopout_filter.py` — 15 tests cubriendo: armado del bloqueo
por lado, wins no arman, desactivación por env, duración = N × timeframe,
bloqueo/expiración/dirección opuesta, defaults y overrides de cooldown, y
presencia de la regla de capitulación en el prompt.

## 6. Cómo medir la mejora

En próximos casos similares buscar en los logs:
- `POST_STOPOUT` / `post_stopout_reentry_block` → el filtro actuó
- `nanofenix_hard_veto:` → el veto actuó
- `CAUTION MODE` con `cooldown=1800s`
- Reasoning de QABBA mencionando "capitulation climax" en extremos
