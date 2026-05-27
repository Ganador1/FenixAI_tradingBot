"""Test simple del sistema de normalización sin dependencias externas."""

import sys
import os
from datetime import datetime, timezone

# Agregar src al path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

try:
    from pipeline.data_normalizer import (
        DataNormalizer,
        OHLCVNormalizer,
        IndicatorNormalizer,
        SentimentNormalizer,
        NormalizationConfig,
        DataType,
        normalize,
        normalize_ohlcv,
        normalize_indicator,
        normalize_sentiment,
    )

    print("✓ Importación exitosa de todos los módulos\n")
except Exception as e:
    print(f"✗ Error en importación: {e}")
    sys.exit(1)


def test_basic_functionality():
    """Test básico de funcionalidad."""
    print("=" * 60)
    print("TEST 1: Funcionalidad básica de OHLCV")
    print("=" * 60)

    normalizer = OHLCVNormalizer()

    # Datos OHLCV de prueba
    ohlcv_data = {
        "timestamp": 1704067200000,
        "open": 42000.50,
        "high": 43500.75,
        "low": 41800.25,
        "close": 42800.00,
        "volume": 1250.50,
    }

    result = normalizer.normalize(ohlcv_data)

    print(f"  Éxito: {result.success}")
    print(f"  Timestamp: {result.data.get('timestamp')}")
    print(f"  Datetime: {result.data.get('datetime')}")
    print(f"  Price Change: {result.data.get('price_change')}")
    print(f"  Price Change %: {result.data.get('price_change_pct', 0):.2f}%")
    print(f"  Candle Range: {result.data.get('candle_range')}")
    print(f"  Candle Body: {result.data.get('candle_body')}")
    print(f"  Errores: {result.errors}")
    print(f"  Advertencias: {result.warnings}")

    assert result.success, f"Error: {result.errors}"
    assert result.data["timestamp"] == 1704067200000
    assert "datetime" in result.data
    # price_change = close - open = 42800.00 - 42000.50 = 799.5
    assert result.data["price_change"] == 799.5
    print("  ✓ Test OHLCV pasado\n")


def test_indicator_normalization():
    """Test de normalización de indicadores."""
    print("=" * 60)
    print("TEST 2: Normalización de Indicadores")
    print("=" * 60)

    normalizer = IndicatorNormalizer()

    # Test RSI
    print("\n  RSI - Sobrecomprado:")
    rsi_overbought = {"rsi": 75.5}
    result = normalizer.normalize(rsi_overbought)
    print(f"    Valor: {result.data.get('rsi')}")
    print(f"    Señal: {result.data.get('signal')}")
    print(f"    Sugerencia: {result.data.get('suggestion')}")
    assert result.data["signal"] == "overbought"
    assert result.data["suggestion"] == "SELL"

    print("\n  RSI - Sobreventa:")
    rsi_oversold = {"rsi": 25.0}
    result = normalizer.normalize(rsi_oversold)
    print(f"    Valor: {result.data.get('rsi')}")
    print(f"    Señal: {result.data.get('signal')}")
    print(f"    Sugerencia: {result.data.get('suggestion')}")
    assert result.data["signal"] == "oversold"
    assert result.data["suggestion"] == "BUY"

    print("\n  RSI - Neutral:")
    rsi_neutral = {"rsi": 50.0}
    result = normalizer.normalize(rsi_neutral)
    print(f"    Valor: {result.data.get('rsi')}")
    print(f"    Señal: {result.data.get('signal')}")
    print(f"    Sugerencia: {result.data.get('suggestion')}")

    # Test MACD
    print("\n  MACD:")
    macd_data = {"macd": 1.5, "signal": 0.5, "histogram": 1.0}
    result = normalizer.normalize(macd_data)
    print(f"    Tipo: {result.data.get('indicator_type')}")
    print(f"    Señal: {result.data.get('signal')}")
    assert result.data["indicator_type"] == "macd"

    print("  ✓ Test Indicadores pasado\n")


def test_sentiment_normalization():
    """Test de normalización de sentimiento."""
    print("=" * 60)
    print("TEST 3: Normalización de Sentimiento")
    print("=" * 60)

    normalizer = SentimentNormalizer()

    test_cases = [
        ({"sentiment": "bullish"}, 1.0, "positive"),
        ({"sentiment": "bearish"}, -1.0, "negative"),
        ({"sentiment": "neutral"}, 0.0, "neutral"),
        ({"score": 75.0}, 0.75, "positive"),
        ({"score": -60.0}, -0.60, "negative"),
        ({"polarity": 0.5}, 0.5, "positive"),
    ]

    for data, expected_sentiment, expected_label in test_cases:
        result = normalizer.normalize(data)
        print(f"\n  Input: {data}")
        print(f"    Normalizado: {result.data.get('normalized_sentiment')}")
        print(f"    Label: {result.data.get('label')}")
        print(f"    Magnitud: {result.data.get('magnitude')}")

        assert abs(result.data["normalized_sentiment"] - expected_sentiment) < 0.01
        assert result.data["label"] == expected_label

    print("  ✓ Test Sentimiento pasado\n")


def test_orchestrator():
    """Test del orquestador principal."""
    print("=" * 60)
    print("TEST 4: Orquestador Principal")
    print("=" * 60)

    orchestrator = DataNormalizer()

    # Test OHLCV
    print("\n  Auto-detección OHLCV:")
    ohlcv = {
        "timestamp": 1704067200000,
        "open": 100.0,
        "high": 110.0,
        "low": 90.0,
        "close": 105.0,
        "volume": 1000.0,
    }
    result = orchestrator.normalize(ohlcv)
    print(f"    Éxito: {result.success}")
    print(f"    Tipo detectado: {orchestrator.detect_data_type(ohlcv)}")
    assert result.success

    # Test Indicador
    print("\n  Auto-detección Indicador:")
    indicator = {"rsi": 65.0}
    result = orchestrator.normalize(indicator)
    print(f"    Éxito: {result.success}")
    print(f"    Tipo detectado: {orchestrator.detect_data_type(indicator)}")
    assert result.success

    # Test Sentimiento
    print("\n  Auto-detección Sentimiento:")
    sentiment = {"sentiment": "positive"}
    result = orchestrator.normalize(sentiment)
    print(f"    Éxito: {result.success}")
    print(f"    Tipo detectado: {orchestrator.detect_data_type(sentiment)}")
    assert result.success

    # Test batch
    print("\n  Normalización en batch:")
    batch = [
        {
            "timestamp": 1704067200000,
            "open": 100.0,
            "high": 110.0,
            "low": 90.0,
            "close": 105.0,
            "volume": 1000.0,
        },
        {"rsi": 70.0},
        {"sentiment": "bullish"},
    ]
    results = orchestrator.normalize_batch(batch)
    print(f"    Resultados: {len(results)}")
    print(f"    Todos exitosos: {all(r.success for r in results)}")
    assert len(results) == 3
    assert all(r.success for r in results)

    print("  ✓ Test Orquestador pasado\n")


def test_edge_cases():
    """Test de casos edge."""
    print("=" * 60)
    print("TEST 5: Casos Edge")
    print("=" * 60)

    normalizer = DataNormalizer()

    # Test None
    print("\n  Datos None:")
    result = normalizer.normalize(None)
    print(f"    Éxito: {result.success}")
    print(f"    Errores: {result.errors}")
    assert not result.success
    assert "Datos nulos" in result.errors[0]

    # Test timestamp en segundos
    print("\n  Timestamp en segundos:")
    ohlcv_sec = {
        "timestamp": 1704067200,  # Segundos
        "open": 100.0,
        "high": 110.0,
        "low": 90.0,
        "close": 105.0,
        "volume": 1000.0,
    }
    result = normalizer.normalize(ohlcv_sec)
    print(f"    Timestamp convertido: {result.data.get('timestamp')}")
    # Debe convertir a ms
    assert result.data["timestamp"] == 1704067200000

    # Test timestamp ISO
    print("\n  Timestamp ISO:")
    ohlcv_iso = {
        "timestamp": "2024-01-01T00:00:00Z",
        "open": 100.0,
        "high": 110.0,
        "low": 90.0,
        "close": 105.0,
        "volume": 1000.0,
    }
    result = normalizer.normalize(ohlcv_iso)
    print(f"    Timestamp convertido: {result.data.get('timestamp')}")
    print(f"    Datetime: {result.data.get('datetime')}")
    assert result.success

    # Test precios con strings
    print("\n  Precios como strings:")
    ohlcv_str = {
        "timestamp": 1704067200000,
        "open": "42000.50",
        "high": "43500.75",
        "low": "41800.25",
        "close": "42800.00",
        "volume": "1250.50",
    }
    result = normalizer.normalize(ohlcv_str)
    print(f"    Éxito: {result.success}")
    print(f"    Open: {result.data.get('open')} (tipo: {type(result.data.get('open'))})")
    assert result.success

    # Test datos desconocidos
    print("\n  Datos desconocidos:")
    unknown = {"unknown_field": "value", "another": 123}
    result = normalizer.normalize(unknown)
    print(f"    Éxito: {result.success}")
    print(f"    Errores: {result.errors}")
    print(f"    Warnings: {result.warnings}")
    assert not result.success

    print("  ✓ Test Edge Cases pasado\n")


def test_configurations():
    """Test de diferentes configuraciones."""
    print("=" * 60)
    print("TEST 6: Configuraciones")
    print("=" * 60)

    # Test strict mode
    print("\n  Modo estricto:")
    config_strict = NormalizationConfig(strict_mode=True)
    strict_normalizer = OHLCVNormalizer(config_strict)

    # High < Low - debería fallar en modo estricto
    invalid_ohlcv = {
        "timestamp": 1704067200000,
        "open": 100.0,
        "high": 90.0,  # Menor que low
        "low": 110.0,
        "close": 100.0,
        "volume": 1000.0,
    }
    result = strict_normalizer.normalize(invalid_ohlcv)
    print(f"    Éxito: {result.success}")
    print(f"    Errores: {result.errors}")
    # En modo estricto, debería fallar por la inconsistencia de precios

    # Test sin preservar original
    print("\n  Sin preservar datos originales:")
    config_no_preserve = NormalizationConfig(preserve_original=False)
    no_preserve_normalizer = OHLCVNormalizer(config_no_preserve)

    valid_ohlcv = {
        "timestamp": 1704067200000,
        "open": 100.0,
        "high": 110.0,
        "low": 90.0,
        "close": 105.0,
        "volume": 1000.0,
    }
    result = no_preserve_normalizer.normalize(valid_ohlcv)
    print(f"    Original preservado: {result.original_data is not None}")
    assert result.original_data is None

    # Test precisión decimal
    print("\n  Precisión decimal personalizada:")
    config_precision = NormalizationConfig(decimal_precision=2)
    precision_normalizer = IndicatorNormalizer(config_precision)

    indicator = {"value": 65.123456789}
    result = precision_normalizer.normalize(indicator)
    print(f"    Valor original: 65.123456789")
    print(f"    Valor normalizado: {result.data.get('value')}")
    assert result.data["value"] == 65.12  # Redondeado a 2 decimales

    print("  ✓ Test Configuraciones pasado\n")


def test_real_world_scenarios():
    """Test con escenarios del mundo real."""
    print("=" * 60)
    print("TEST 7: Escenarios del Mundo Real")
    print("=" * 60)

    orchestrator = DataNormalizer()

    # Múltiples indicadores técnicos
    print("\n  Múltiples indicadores técnicos:")
    indicators = [
        {"rsi": 65.5, "period": 14},
        {"macd": 1.5, "signal_line": 0.5, "histogram": 1.0},
        {"ema_20": 42500.0, "ema_50": 42000.0},
        {"bb_upper": 45000.0, "bb_middle": 43000.0, "bb_lower": 41000.0, "bandwidth": 0.05},
        {"atr": 850.0, "period": 14},
    ]

    results = orchestrator.normalize_batch(indicators)
    for i, result in enumerate(results):
        print(f"    Indicador {i + 1}: {result.data.get('indicator_type', 'unknown')}")

    assert len(results) == 5
    assert all(r.success for r in results)

    # Tokens de bajo valor (SHIB, DOGE, etc.)
    print("\n  Tokens de bajo valor:")
    low_value_token = {
        "timestamp": 1704067200000,
        "open": 0.00001234567890,
        "high": 0.00001345678901,
        "low": 0.00001123456789,
        "close": 0.00001298765432,
        "volume": 1500000000000.0,
    }
    result = orchestrator.normalize(low_value_token)
    print(f"    Éxito: {result.success}")
    print(f"    Precisión mantenida: {result.data.get('close')}")
    assert result.success

    # Datos mezclados de diferentes fuentes
    print("\n  Datos mezclados de diferentes fuentes:")
    mixed_data = [
        {
            "timestamp": 1704067200000,
            "open": 100.0,
            "high": 110.0,
            "low": 90.0,
            "close": 105.0,
            "volume": 1000.0,
        },
        {"rsi": 70.0},
        {"sentiment": "positive", "score": 0.85},
        {
            "timestamp": 1704153600000,
            "open": 105.0,
            "high": 115.0,
            "low": 100.0,
            "close": 110.0,
            "volume": 1200.0,
        },
        {"macd": 2.0, "signal": 1.0, "histogram": 1.0},
    ]

    results = orchestrator.normalize_batch(mixed_data)
    types = [orchestrator.detect_data_type(mixed_data[i]) for i in range(len(mixed_data))]

    print(f"    Tipos detectados:")
    for i, t in enumerate(types):
        print(f"      {i + 1}. {t}")

    print(f"    Todos normalizados exitosamente: {all(r.success for r in results)}")
    assert all(r.success for r in results)

    print("  ✓ Test Escenarios Reales pasado\n")


def run_all_tests():
    """Ejecutar todos los tests."""
    print("\n" + "=" * 60)
    print("SISTEMA DE NORMALIZACIÓN DE DATOS FENIX AI")
    print("=" * 60)
    print(f"Fecha: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 60 + "\n")

    try:
        test_basic_functionality()
        test_indicator_normalization()
        test_sentiment_normalization()
        test_orchestrator()
        test_edge_cases()
        test_configurations()
        test_real_world_scenarios()

        print("\n" + "=" * 60)
        print("✓ TODOS LOS TESTS PASARON EXITOSAMENTE")
        print("=" * 60)
        print("\nEl sistema de normalización está funcionando correctamente.")
        print("\nCaracterísticas probadas:")
        print("  • Normalización OHLCV con metadatos derivados")
        print("  • Normalización de indicadores técnicos con señales")
        print("  • Normalización de sentimiento a rango [-1, 1]")
        print("  • Auto-detección de tipos de datos")
        print("  • Procesamiento batch")
        print("  • Múltiples formatos de timestamp")
        print("  • Modos estricto y no-estricto")
        print("  • Preservación opcional de datos originales")
        print("  • Configuración de precisión decimal")
        print("  • Manejo de casos edge")
        print()

        return True

    except AssertionError as e:
        print(f"\n✗ TEST FALLIDO: {e}")
        return False
    except Exception as e:
        print(f"\n✗ ERROR INESPERADO: {e}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
