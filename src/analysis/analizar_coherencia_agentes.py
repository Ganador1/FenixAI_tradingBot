#!/usr/bin/env python3
"""
Análisis de coherencia entre las respuestas de los agentes y los indicadores recibidos
"""


def analizar_coherencia_technical_agent():
    """Analiza la coherencia del agente técnico"""
    print("=== ANÁLISIS AGENTE TÉCNICO ===")

    # Datos del prompt técnico
    indicadores_tecnicos = {
        "precio_actual": 113445.50,
        "rsi": 42.52,
        "macd": 33.69,
        "signal": 70.06,
        "adx": 13.37,
        "atr": 139.38,
        "volumen_vs_promedio": 1.75,
        "direccion_tendencia": "BEARISH",
        "fuerza_tendencia": 13.4,
        "score_confluencia": 0.518
    }

    # Respuesta del agente
    respuesta_agente = {
        "signal": "SELL",
        "confidence": 0.7,
        "reasoning": "El analista ha identificado una dirección de tendencia bearish, lo que indica que el mercado tiende a subir y luego caer. Los indicadores como RSI, MACD, ADX, Bollinger Bands, Volume, y ATR están en zonas que sugieren una tendencia bearish con volatilidad baja. El RSI está por debajo de 50, lo que usualmente se interpreta como un level underbought, lo que podría estar abriéndose un oportunidad de compra. Sin embargo, el MACD está por debajo de su level de signal, lo que es un signo negativo para las compras. Además, el volumen está por encima de su level de promedio, lo que podría indicar una fuerza adicional de las compras. Sin embargo, el score de confluencia es de 0.518, lo que está por debajo de lo ideal, lo que reduce la confianza en los indicadores. Finalmente, aunque hay un equilibrio entre los factores de riesgo, la tendencia general se mantiene en alto, lo que hace más probable que el mercado caiga, lo que lo hace más favorable para vender."
    }

    # Análisis de coherencia
    coherencia = {}

    # 1. RSI Analysis
    rsi_val = indicadores_tecnicos["rsi"]
    if "RSI está por debajo de 50" in respuesta_agente["reasoning"]:
        coherencia["RSI"] = {
            "correcto": rsi_val < 50,
            "valor_real": rsi_val,
            "interpretacion_agente": "por debajo de 50, underbought",
            "coherencia": "CORRECTA" if rsi_val < 50 else "INCORRECTA"
        }

    # 2. MACD Analysis
    macd_val = indicadores_tecnicos["macd"]
    signal_val = indicadores_tecnicos["signal"]
    if "MACD está por debajo de su level de signal" in respuesta_agente["reasoning"]:
        coherencia["MACD"] = {
            "correcto": macd_val < signal_val,
            "valor_real": f"MACD: {macd_val}, Signal: {signal_val}",
            "interpretacion_agente": "MACD por debajo de signal",
            "coherencia": "CORRECTA" if macd_val < signal_val else "INCORRECTA"
        }

    # 3. Volumen Analysis
    vol_ratio = indicadores_tecnicos["volumen_vs_promedio"]
    if "volumen está por encima de su level de promedio" in respuesta_agente["reasoning"]:
        coherencia["Volumen"] = {
            "correcto": vol_ratio > 1.0,
            "valor_real": f"{vol_ratio}x promedio",
            "interpretacion_agente": "por encima del promedio",
            "coherencia": "CORRECTA" if vol_ratio > 1.0 else "INCORRECTA"
        }

    # 4. Tendencia Analysis
    tendencia = indicadores_tecnicos["direccion_tendencia"]
    if "dirección de tendencia bearish" in respuesta_agente["reasoning"]:
        coherencia["Tendencia"] = {
            "correcto": tendencia == "BEARISH",
            "valor_real": tendencia,
            "interpretacion_agente": "bearish",
            "coherencia": "CORRECTA" if tendencia == "BEARISH" else "INCORRECTA"
        }

    # 5. Score de confluencia
    score = indicadores_tecnicos["score_confluencia"]
    if "score de confluencia es de 0.518" in respuesta_agente["reasoning"]:
        coherencia["Score_Confluencia"] = {
            "correcto": abs(score - 0.518) < 0.001,
            "valor_real": score,
            "interpretacion_agente": "0.518",
            "coherencia": "CORRECTA" if abs(score - 0.518) < 0.001 else "INCORRECTA"
        }

    # Errores conceptuales detectados
    errores_conceptuales = []

    if "mercado tiende a subir y luego caer" in respuesta_agente["reasoning"]:
        errores_conceptuales.append("CONFUSIÓN: 'tendencia bearish' no significa que 'tiende a subir y luego caer'")

    if "underbought" in respuesta_agente["reasoning"]:
        errores_conceptuales.append("ERROR TERMINOLÓGICO: 'underbought' debería ser 'oversold'")

    if "tendencia general se mantiene en alto" in respuesta_agente["reasoning"]:
        errores_conceptuales.append("CONTRADICCIÓN: dice tendencia bearish pero luego 'se mantiene en alto'")

    return coherencia, errores_conceptuales, respuesta_agente

def analizar_coherencia_qabba_agent():
    """Analiza la coherencia del agente QABBA"""
    print("\n=== ANÁLISIS AGENTE QABBA ===")

    # Datos del input QABBA
    indicadores_qabba = {
        "precio_actual": 113445.50,
        "percent_b": 0.144,
        "bandwidth": 0.0049,
        "squeeze_status": False,
        "band_position": "LOWER",
        "desequilibrio_flujo": 37.858,
        "spread": "TIGHT",
        "liquidez": "HIGH",
        "actividad_institucional": "MEDIUM",
        "regimen_volatilidad": "LOW",
        "direccion_momentum": "BEARISH",
        "fuerza_momentum": 0.71,
        "score_confluencia": 0.225,
        "senales_bullish": 3,
        "senales_bearish": 1
    }

    # Respuesta del agente QABBA
    respuesta_qabba = {
        "signal": "SELL_QABBA",
        "confidence": 0.6,
        "reasoning": "La microestructura muestra un desequilibrio de flujo de órdenes positivo, lo que indica un aumento de demanda. Además, los indicadores como el MACD, Aroon y EMA sugieren una tendencia bullish, pero el Bollinger %B está por debajo de 0.5, lo que podría indicar una regresión. La volatilidad GARCH está en un régimen LOW, lo que reduce la confianza en las predicciones. Sin embargo, el MACD y Aroon sugieren una tendencia bullish, lo que podría contrarrestar el impacto de los desequilibrios de órdenes."
    }

    # Análisis de coherencia
    coherencia = {}

    # 1. Desequilibrio de flujo
    flujo = indicadores_qabba["desequilibrio_flujo"]
    if "desequilibrio de flujo de órdenes positivo" in respuesta_qabba["reasoning"]:
        coherencia["Flujo_Ordenes"] = {
            "correcto": flujo > 0,
            "valor_real": flujo,
            "interpretacion_agente": "positivo (aumento demanda)",
            "coherencia": "CORRECTA" if flujo > 0 else "INCORRECTA"
        }

    # 2. Bollinger %B
    percent_b = indicadores_qabba["percent_b"]
    if "Bollinger %B está por debajo de 0.5" in respuesta_qabba["reasoning"]:
        coherencia["Percent_B"] = {
            "correcto": percent_b < 0.5,
            "valor_real": percent_b,
            "interpretacion_agente": "por debajo de 0.5",
            "coherencia": "CORRECTA" if percent_b < 0.5 else "INCORRECTA"
        }

    # 3. Régimen de volatilidad
    regimen = indicadores_qabba["regimen_volatilidad"]
    if "volatilidad GARCH está en un régimen LOW" in respuesta_qabba["reasoning"]:
        coherencia["Regimen_Volatilidad"] = {
            "correcto": regimen == "LOW",
            "valor_real": regimen,
            "interpretacion_agente": "LOW",
            "coherencia": "CORRECTA" if regimen == "LOW" else "INCORRECTA"
        }

    # Contradicciones detectadas
    contradicciones = []

    # Contradicción principal: señal SELL pero argumentos bullish
    if respuesta_qabba["signal"] == "SELL_QABBA":
        if "tendencia bullish" in respuesta_qabba["reasoning"]:
            contradicciones.append("CONTRADICCIÓN MAYOR: Señal SELL pero argumenta tendencia bullish")

    # Contradicción en datos: más señales bullish que bearish
    senales_bull = indicadores_qabba["senales_bullish"]
    senales_bear = indicadores_qabba["senales_bearish"]
    if senales_bull > senales_bear:
        contradicciones.append(f"CONTRADICCIÓN DATOS: {senales_bull} señales bullish vs {senales_bear} bearish, pero decide SELL")

    # Contradicción momentum
    momentum_dir = indicadores_qabba["direccion_momentum"]
    if momentum_dir == "BEARISH" and "tendencia bullish" in respuesta_qabba["reasoning"]:
        contradicciones.append("CONTRADICCIÓN: Momentum BEARISH en datos pero argumenta tendencia bullish")

    return coherencia, contradicciones, respuesta_qabba

def generar_reporte_final():
    """Genera el reporte final de coherencia"""
    print("\n" + "="*80)
    print("REPORTE FINAL DE COHERENCIA DE AGENTES")
    print("="*80)

    # Analizar agente técnico
    coherencia_tech, errores_tech, resp_tech = analizar_coherencia_technical_agent()

    # Analizar agente QABBA
    coherencia_qabba, contradicciones_qabba, resp_qabba = analizar_coherencia_qabba_agent()

    print("\n📊 RESUMEN AGENTE TÉCNICO:")
    print(f"Señal: {resp_tech['signal']} (Confianza: {resp_tech['confidence']})")

    correctas_tech = sum(1 for item in coherencia_tech.values() if item['coherencia'] == 'CORRECTA')
    total_tech = len(coherencia_tech)

    print(f"Coherencia con datos: {correctas_tech}/{total_tech} indicadores correctos")

    for indicador, analisis in coherencia_tech.items():
        status = "✅" if analisis['coherencia'] == 'CORRECTA' else "❌"
        print(f"  {status} {indicador}: {analisis['interpretacion_agente']} (Real: {analisis['valor_real']})")

    if errores_tech:
        print("\n🚨 ERRORES CONCEPTUALES TÉCNICO:")
        for error in errores_tech:
            print(f"  ❌ {error}")

    print("\n📊 RESUMEN AGENTE QABBA:")
    print(f"Señal: {resp_qabba['signal']} (Confianza: {resp_qabba['confidence']})")

    correctas_qabba = sum(1 for item in coherencia_qabba.values() if item['coherencia'] == 'CORRECTA')
    total_qabba = len(coherencia_qabba)

    print(f"Coherencia con datos: {correctas_qabba}/{total_qabba} indicadores correctos")

    for indicador, analisis in coherencia_qabba.items():
        status = "✅" if analisis['coherencia'] == 'CORRECTA' else "❌"
        print(f"  {status} {indicador}: {analisis['interpretacion_agente']} (Real: {analisis['valor_real']})")

    if contradicciones_qabba:
        print("\n🚨 CONTRADICCIONES QABBA:")
        for contradiccion in contradicciones_qabba:
            print(f"  ❌ {contradiccion}")

    # Evaluación general
    print("\n🎯 EVALUACIÓN GENERAL:")

    # Agente Técnico
    porcentaje_tech = (correctas_tech / total_tech * 100) if total_tech > 0 else 0
    calidad_tech = "BUENA" if porcentaje_tech >= 80 else "REGULAR" if porcentaje_tech >= 60 else "MALA"
    print(f"Agente Técnico: {calidad_tech} ({porcentaje_tech:.1f}% coherencia)")

    # Agente QABBA
    porcentaje_qabba = (correctas_qabba / total_qabba * 100) if total_qabba > 0 else 0
    calidad_qabba = "BUENA" if porcentaje_qabba >= 80 and len(contradicciones_qabba) == 0 else "REGULAR" if porcentaje_qabba >= 60 else "MALA"
    print(f"Agente QABBA: {calidad_qabba} ({porcentaje_qabba:.1f}% coherencia, {len(contradicciones_qabba)} contradicciones)")

    # Recomendaciones
    print("\n💡 RECOMENDACIONES:")

    if errores_tech:
        print("📈 AGENTE TÉCNICO:")
        print("  - Revisar terminología técnica (oversold vs underbought)")
        print("  - Clarificar conceptos de tendencia bearish")
        print("  - Evitar contradicciones en el razonamiento")

    if contradicciones_qabba:
        print("📊 AGENTE QABBA:")
        print("  - Alinear la decisión con el análisis de señales")
        print("  - Considerar el peso de señales bullish vs bearish")
        print("  - Revisar lógica de decisión cuando hay conflictos")

    print("\n✅ CONCLUSIÓN:")
    if porcentaje_tech >= 70 and porcentaje_qabba >= 70 and len(contradicciones_qabba) <= 1:
        print("Los agentes muestran coherencia aceptable con los datos recibidos.")
    else:
        print("Los agentes necesitan mejoras en coherencia y consistencia lógica.")

if __name__ == "__main__":
    generar_reporte_final()
