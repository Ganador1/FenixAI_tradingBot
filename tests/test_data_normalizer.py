"""Tests para el Sistema de Normalización de Datos Fenix AI."""

import pytest
import sys
import os
from decimal import Decimal
from datetime import datetime, timezone

# Agregar src al path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from src.pipeline.data_normalizer import (
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
    NormalizationError,
    ValidationError,
)


class TestNormalizationConfig:
    """Tests para la configuración de normalización."""

    def test_default_config(self):
        config = NormalizationConfig()
        assert config.strict_mode == False
        assert config.preserve_original == True
        assert config.decimal_precision == 8
        assert config.timezone == "UTC"

    def test_custom_config(self):
        config = NormalizationConfig(strict_mode=True, decimal_precision=4, volume_precision=2)
        assert config.strict_mode == True
        assert config.decimal_precision == 4
        assert config.volume_precision == 2


class TestOHLCVNormalizer:
    """Tests para el normalizador OHLCV."""

    def test_can_handle_valid_ohlcv(self):
        normalizer = OHLCVNormalizer()

        # Datos OHLCV completos
        valid_data = {
            "timestamp": 1704067200000,
            "open": 42000.50,
            "high": 43500.75,
            "low": 41800.25,
            "close": 42800.00,
            "volume": 1250.50,
        }
        assert normalizer.can_handle(valid_data) == True

    def test_can_handle_invalid_data(self):
        normalizer = OHLCVNormalizer()

        # Datos que no son OHLCV
        invalid_data = {"sentiment": "positive", "score": 0.8}
        assert normalizer.can_handle(invalid_data) == False

        # Datos no-dict
        assert normalizer.can_handle("string") == False
        assert normalizer.can_handle(None) == False

    def test_normalize_complete_ohlcv(self):
        normalizer = OHLCVNormalizer()

        data = {
            "timestamp": 1704067200000,
            "open": 42000.50,
            "high": 43500.75,
            "low": 41800.25,
            "close": 42800.00,
            "volume": 1250.50,
            "quote_volume": 53500000.00,
        }

        result = normalizer.normalize(data)

        assert result.success == True
        assert result.data["timestamp"] == 1704067200000
        assert "datetime" in result.data
        assert result.data["price_change"] == 799.5
        assert result.data["price_change_pct"] == pytest.approx(1.9047, rel=0.01)
        assert "candle_range" in result.data
        assert result.data["candle_range"] == 1700.50

    def test_normalize_ohlcv_with_seconds_timestamp(self):
        normalizer = OHLCVNormalizer()

        # Timestamp en segundos (más pequeño)
        data = {
            "timestamp": 1704067200,  # Segundos
            "open": 100.0,
            "high": 110.0,
            "low": 95.0,
            "close": 105.0,
            "volume": 1000.0,
        }

        result = normalizer.normalize(data)

        # Debe convertir a milisegundos
        assert result.data["timestamp"] == 1704067200000

    def test_normalize_ohlcv_with_iso_timestamp(self):
        normalizer = OHLCVNormalizer()

        data = {
            "timestamp": "2024-01-01T00:00:00Z",
            "open": 100.0,
            "high": 110.0,
            "low": 95.0,
            "close": 105.0,
            "volume": 1000.0,
        }

        result = normalizer.normalize(data)

        assert result.success == True
        assert "timestamp" in result.data
        assert isinstance(result.data["timestamp"], int)

    def test_ohlcv_price_consistency_validation(self):
        config = NormalizationConfig(strict_mode=True)
        normalizer = OHLCVNormalizer(config)

        # High < Low - debería fallar en modo estricto
        invalid_data = {
            "timestamp": 1704067200000,
            "open": 100.0,
            "high": 90.0,  # Menor que low
            "low": 110.0,
            "close": 100.0,
            "volume": 1000.0,
        }

        result = normalizer.normalize(invalid_data)

        assert result.success == False
        assert len(result.errors) > 0

    def test_ohlcv_missing_fields_non_strict(self):
        config = NormalizationConfig(strict_mode=False)
        normalizer = OHLCVNormalizer(config)

        # Faltan algunos campos
        incomplete_data = {"timestamp": 1704067200000, "close": 100.0, "volume": 1000.0}

        result = normalizer.normalize(incomplete_data)

        # En modo no-estricto, no debería fallar
        assert result.success == True
        assert len(result.warnings) > 0  # Pero debería advertir


class TestIndicatorNormalizer:
    """Tests para el normalizador de indicadores."""

    def test_can_handle_indicator_data(self):
        normalizer = IndicatorNormalizer()

        rsi_data = {"rsi": 65.5, "timestamp": 1704067200000}
        assert normalizer.can_handle(rsi_data) == True

        macd_data = {"macd": 1.5, "signal": 0.5, "histogram": 1.0}
        assert normalizer.can_handle(macd_data) == True

    def test_normalize_rsi_indicator(self):
        normalizer = IndicatorNormalizer()

        # RSI sobrecomprado
        data = {"rsi": 75.5, "timestamp": 1704067200000}
        result = normalizer.normalize(data)

        assert result.success == True
        assert result.data["indicator_type"] == "rsi"
        assert result.data["signal"] == "overbought"
        assert result.data["suggestion"] == "SELL"

    def test_normalize_rsi_oversold(self):
        normalizer = IndicatorNormalizer()

        data = {"rsi": 25.0, "timestamp": 1704067200000}
        result = normalizer.normalize(data)

        assert result.data["signal"] == "oversold"
        assert result.data["suggestion"] == "BUY"

    def test_normalize_macd_bullish(self):
        normalizer = IndicatorNormalizer()

        data = {"macd": 1.5, "signal": 0.5, "histogram": 1.0}
        result = normalizer.normalize(data)

        assert result.success == True
        assert result.data["indicator_type"] == "macd"

    def test_normalize_decimal_precision(self):
        config = NormalizationConfig(decimal_precision=4)
        normalizer = IndicatorNormalizer(config)

        data = {"value": 65.123456789, "type": "rsi"}
        result = normalizer.normalize(data)

        # Verificar que el valor tiene la precisión correcta
        assert result.data["value"] == 65.1235  # Redondeado a 4 decimales


class TestSentimentNormalizer:
    """Tests para el normalizador de sentimiento."""

    def test_can_handle_sentiment_data(self):
        normalizer = SentimentNormalizer()

        assert normalizer.can_handle({"sentiment": "positive"}) == True
        assert normalizer.can_handle({"score": 0.8}) == True
        assert normalizer.can_handle({"polarity": 0.5}) == True
        assert normalizer.can_handle({"label": "bullish"}) == True

    def test_normalize_text_sentiment(self):
        normalizer = SentimentNormalizer()

        # Sentimiento positivo como texto
        data = {"sentiment": "bullish"}
        result = normalizer.normalize(data)

        assert result.success == True
        assert result.data["normalized_sentiment"] == 1.0
        assert result.data["label"] == "positive"
        assert result.data["magnitude"] == 1.0

    def test_normalize_negative_sentiment(self):
        normalizer = SentimentNormalizer()

        data = {"sentiment": "bearish"}
        result = normalizer.normalize(data)

        assert result.data["normalized_sentiment"] == -1.0
        assert result.data["label"] == "negative"

    def test_normalize_score_scaling(self):
        normalizer = SentimentNormalizer()

        # Score en escala 0-100
        data = {"score": 75.0}
        result = normalizer.normalize(data)

        assert result.data["normalized_sentiment"] == 0.75

    def test_normalize_negative_score_scaling(self):
        normalizer = SentimentNormalizer()

        data = {"score": -60.0}
        result = normalizer.normalize(data)

        assert result.data["normalized_sentiment"] == -0.60

    def test_normalize_neutral_sentiment(self):
        normalizer = SentimentNormalizer()

        data = {"sentiment": "neutral"}
        result = normalizer.normalize(data)

        assert result.data["normalized_sentiment"] == 0.0
        assert result.data["label"] == "neutral"


class TestDataNormalizer:
    """Tests para el orquestador principal."""

    def test_normalize_ohlcv_via_orchestrator(self):
        orchestrator = DataNormalizer()

        data = {
            "timestamp": 1704067200000,
            "open": 42000.0,
            "high": 43000.0,
            "low": 41000.0,
            "close": 42500.0,
            "volume": 1000.0,
        }

        result = orchestrator.normalize(data)

        assert result.success == True
        assert "timestamp" in result.data

    def test_normalize_indicator_via_orchestrator(self):
        orchestrator = DataNormalizer()

        data = {"rsi": 70.0, "timestamp": 1704067200000}
        result = orchestrator.normalize(data)

        assert result.success == True
        assert result.data.get("indicator_type") == "rsi"

    def test_normalize_sentiment_via_orchestrator(self):
        orchestrator = DataNormalizer()

        data = {"sentiment": "positive", "score": 0.8}
        result = orchestrator.normalize(data)

        assert result.success == True
        assert "normalized_sentiment" in result.data

    def test_normalize_unknown_data(self):
        orchestrator = DataNormalizer()

        # Datos que no coinciden con ningún normalizador
        data = {"unknown_field": "value", "another_field": 123}
        result = orchestrator.normalize(data)

        assert result.success == False
        assert "_raw" in result.data

    def test_normalize_none_data(self):
        orchestrator = DataNormalizer()

        result = orchestrator.normalize(None)

        assert result.success == False
        assert "Datos nulos" in result.errors

    def test_normalize_batch(self):
        orchestrator = DataNormalizer()

        data_list = [
            {
                "timestamp": 1704067200000,
                "open": 100.0,
                "high": 110.0,
                "low": 90.0,
                "close": 105.0,
                "volume": 1000.0,
            },
            {"rsi": 65.0, "timestamp": 1704067200000},
            {"sentiment": "positive", "score": 0.8},
        ]

        results = orchestrator.normalize_batch(data_list)

        assert len(results) == 3
        assert all(r.success for r in results)

    def test_detect_data_type(self):
        orchestrator = DataNormalizer()

        ohlcv_data = {"timestamp": 1704067200000, "open": 100.0, "close": 105.0, "volume": 1000.0}
        indicator_data = {"rsi": 65.0}
        sentiment_data = {"sentiment": "positive"}

        assert orchestrator.detect_data_type(ohlcv_data) == DataType.MARKET_OHLCV
        assert orchestrator.detect_data_type(indicator_data) == DataType.INDICATOR
        assert orchestrator.detect_data_type(sentiment_data) == DataType.SENTIMENT


class TestConvenienceFunctions:
    """Tests para las funciones de conveniencia."""

    def test_normalize_function(self):
        data = {"rsi": 70.0, "timestamp": 1704067200000}
        result = normalize(data)

        assert result.success == True

    def test_normalize_ohlcv_function(self):
        data = {
            "timestamp": 1704067200000,
            "open": 100.0,
            "high": 110.0,
            "low": 90.0,
            "close": 105.0,
            "volume": 1000.0,
        }
        result = normalize_ohlcv(data)

        assert result.success == True
        assert "price_change" in result.data

    def test_normalize_indicator_function(self):
        data = {"rsi": 65.0}
        result = normalize_indicator(data)

        assert result.success == True
        assert result.data["indicator_type"] == "rsi"

    def test_normalize_sentiment_function(self):
        data = {"sentiment": "bullish"}
        result = normalize_sentiment(data)

        assert result.success == True
        assert result.data["normalized_sentiment"] == 1.0


class TestEdgeCases:
    """Tests para casos edge y manejo de errores."""

    def test_empty_dict(self):
        orchestrator = DataNormalizer()
        result = orchestrator.normalize({})

        # Debería fallar ya que no hay datos para normalizar
        assert result.success == False

    def test_very_large_numbers(self):
        normalizer = OHLCVNormalizer()

        # Precios muy grandes (como SHIB o tokens de bajo valor)
        data = {
            "timestamp": 1704067200000,
            "open": 0.00001234567890,
            "high": 0.00001345678901,
            "low": 0.00001123456789,
            "close": 0.00001298765432,
            "volume": 1500000000000.0,
        }

        result = normalizer.normalize(data)

        assert result.success == True
        # Verificar que se mantuvo la precisión
        assert "price_change" in result.data

    def test_very_small_numbers(self):
        normalizer = OHLCVNormalizer()

        data = {
            "timestamp": 1704067200000,
            "open": 1e-10,
            "high": 1.5e-10,
            "low": 0.8e-10,
            "close": 1.2e-10,
            "volume": 1e20,
        }

        result = normalizer.normalize(data)

        assert result.success == True

    def test_missing_optional_fields(self):
        normalizer = OHLCVNormalizer()

        # Datos OHLCV mínimos
        data = {
            "timestamp": 1704067200000,
            "open": 100.0,
            "high": 110.0,
            "low": 90.0,
            "close": 105.0,
            "volume": 1000.0,
        }

        result = normalizer.normalize(data)

        assert result.success == True
        # No debería tener campos opcionales
        assert "quote_volume" not in result.data

    def test_preserve_original_data(self):
        config = NormalizationConfig(preserve_original=True)
        normalizer = OHLCVNormalizer(config)

        data = {
            "timestamp": 1704067200000,
            "open": 100.0,
            "high": 110.0,
            "low": 90.0,
            "close": 105.0,
            "volume": 1000.0,
        }
        result = normalizer.normalize(data)

        assert result.original_data is not None
        assert result.original_data["timestamp"] == 1704067200000

    def test_not_preserve_original_data(self):
        config = NormalizationConfig(preserve_original=False)
        normalizer = OHLCVNormalizer(config)

        data = {
            "timestamp": 1704067200000,
            "open": 100.0,
            "high": 110.0,
            "low": 90.0,
            "close": 105.0,
            "volume": 1000.0,
        }
        result = normalizer.normalize(data)

        assert result.original_data is None


class TestRealWorldScenarios:
    """Tests con escenarios del mundo real."""

    def test_binance_ohlcv_format(self):
        """Test con formato típico de Binance."""
        normalizer = OHLCVNormalizer()

        # Formato de Binance: [timestamp, open, high, low, close, volume, close_time, ...]
        # Pero también tienen formato objeto
        binance_data = {
            "openTime": 1704067200000,
            "open": "42000.50",
            "high": "43500.75",
            "low": "41800.25",
            "close": "42800.00",
            "volume": "1250.50",
            "quoteVolume": "53500000.00",
            "count": 15000,
        }

        result = normalizer.normalize(binance_data)

        # En modo no-estricto, debería normalizar lo que puede
        assert result.success == True or len(result.warnings) > 0

    def test_coinbase_ohlcv_format(self):
        """Test con formato típico de Coinbase."""
        normalizer = OHLCVNormalizer()

        coinbase_data = {
            "time": "2024-01-01T00:00:00Z",
            "low": 41800.25,
            "high": 43500.75,
            "open": 42000.50,
            "close": 42800.00,
            "volume": 1250.50,
        }

        result = normalizer.normalize(coinbase_data)

        assert result.success == True
        assert "timestamp" in result.data

    def test_technical_indicators_batch(self):
        """Test normalizando múltiples indicadores técnicos."""
        orchestrator = DataNormalizer()

        indicators = [
            {"rsi": 65.5, "period": 14},
            {"macd": 1.5, "signal_line": 0.5, "histogram": 1.0},
            {"ema_20": 42500.0, "ema_50": 42000.0},
            {"bb_upper": 45000.0, "bb_middle": 43000.0, "bb_lower": 41000.0, "bandwidth": 0.05},
            {"atr": 850.0, "period": 14},
        ]

        results = orchestrator.normalize_batch(indicators)

        assert len(results) == 5
        assert all(r.success for r in results)

        # Verificar que se detectaron los tipos correctos
        assert results[0].data.get("indicator_type") == "rsi"
        assert results[1].data.get("indicator_type") == "macd"
        assert results[3].data.get("indicator_type") == "bollinger_bands"
        assert results[4].data.get("indicator_type") == "atr"

    def test_mixed_data_batch(self):
        """Test con datos mezclados de diferentes tipos."""
        orchestrator = DataNormalizer()

        mixed_data = [
            # OHLCV
            {
                "timestamp": 1704067200000,
                "open": 100.0,
                "high": 110.0,
                "low": 90.0,
                "close": 105.0,
                "volume": 1000.0,
            },
            # Indicador
            {"rsi": 70.0},
            # Sentimiento
            {"sentiment": "positive", "score": 0.85},
            # Otro OHLCV
            {
                "timestamp": 1704153600000,
                "open": 105.0,
                "high": 115.0,
                "low": 100.0,
                "close": 110.0,
                "volume": 1200.0,
            },
            # Indicador MACD
            {"macd": 2.0, "signal": 1.0, "histogram": 1.0},
        ]

        results = orchestrator.normalize_batch(mixed_data)

        assert len(results) == 5
        assert all(r.success for r in results)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
