"""Sistema de Normalización de Datos Fenix AI

Este módulo proporciona un sistema completo para normalizar diferentes tipos de datos:
- Datos de mercado (OHLCV, orderbook, trades)
- Indicadores técnicos (diferentes escalas y formatos)
- Decisiones de trading (heterogéneas entre agentes)
- Datos de sentimiento y noticias
- Configuraciones de diferentes proveedores

Arquitectura:
- DataNormalizer: Clase principal orquestadora
- NormalizerRegistry: Registro de normalizadores especializados
- NormalizerBase: Clase base para normalizadores específicos
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Any, Generic, TypeVar

logger = logging.getLogger(__name__)


class NormalizationError(Exception):
    """Excepción base para errores de normalización."""

    pass


class ValidationError(NormalizationError):
    """Error de validación durante la normalización."""

    pass


class TransformationError(NormalizationError):
    """Error en la transformación de datos."""

    pass


class DataType(str, Enum):
    """Tipos de datos soportados para normalización."""

    MARKET_OHLCV = "market_ohlcv"
    MARKET_ORDERBOOK = "market_orderbook"
    MARKET_TRADES = "market_trades"
    INDICATOR = "indicator"
    DECISION = "decision"
    SENTIMENT = "sentiment"
    NEWS = "news"
    CONFIG = "config"


@dataclass
class NormalizationConfig:
    """Configuración para el proceso de normalización."""

    # Opciones generales
    strict_mode: bool = False  # Si True, falla en datos inválidos; si False, usa defaults
    preserve_original: bool = True  # Mantener datos originales bajo clave '_original'
    add_metadata: bool = True  # Agregar metadatos de normalización

    # Opciones numéricas
    decimal_precision: int = 8  # Precisión para decimales (crypto)
    price_precision: int = 2  # Precisión para precios fiat
    volume_precision: int = 6  # Precisión para volúmenes

    # Opciones temporales
    timezone: str = "UTC"
    timestamp_unit: str = "ms"  # 'ms', 's', 'iso'

    # Opciones de validación
    min_confidence: float = 0.0
    max_confidence: float = 1.0
    valid_actions: tuple = ("BUY", "SELL", "HOLD", "VETO", "UNKNOWN")


@dataclass
class NormalizationResult:
    """Resultado del proceso de normalización."""

    data: dict[str, Any]
    success: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    original_data: dict[str, Any] | None = None


T = TypeVar("T")


class NormalizerBase(ABC, Generic[T]):
    """Clase base para normalizadores especializados."""

    def __init__(self, config: NormalizationConfig | None = None):
        self.config = config or NormalizationConfig()
        self._validators: list[Callable[[T], bool]] = []
        self._transformers: list[Callable[[T], T]] = []

    @abstractmethod
    def can_handle(self, data: Any) -> bool:
        """Determina si este normalizador puede manejar los datos."""
        pass

    @abstractmethod
    def normalize(self, data: T) -> NormalizationResult:
        """Normaliza los datos y retorna el resultado."""
        pass

    def add_validator(self, validator: Callable[[T], bool]) -> None:
        """Agrega un validador al pipeline."""
        self._validators.append(validator)

    def add_transformer(self, transformer: Callable[[T], T]) -> None:
        """Agrega un transformador al pipeline."""
        self._transformers.append(transformer)

    def _apply_validators(self, data: T) -> list[str]:
        """Aplica todos los validadores y retorna errores."""
        errors = []
        for validator in self._validators:
            try:
                if not validator(data):
                    errors.append(f"Validación fallida: {validator.__name__}")
            except Exception as e:
                errors.append(f"Error en validador {validator.__name__}: {e}")
        return errors

    def _apply_transformers(self, data: T) -> T:
        """Aplica todos los transformadores en secuencia."""
        for transformer in self._transformers:
            try:
                data = transformer(data)
            except Exception as e:
                raise TransformationError(f"Error en transformador {transformer.__name__}: {e}")
        return data


class OHLCVNormalizer(NormalizerBase[dict[str, Any]]):
    """Normalizador para datos de velas OHLCV."""

    REQUIRED_FIELDS = {"timestamp", "open", "high", "low", "close", "volume"}
    OPTIONAL_FIELDS = {"quote_volume", "trades_count", "taker_buy_base", "taker_buy_quote"}

    def __init__(self, config: NormalizationConfig | None = None):
        super().__init__(config)
        self._setup_validators()
        self._setup_transformers()

    def _setup_validators(self) -> None:
        self.add_validator(self._validate_required_fields)
        self.add_validator(self._validate_price_consistency)
        self.add_validator(self._validate_volume_positive)

    def _setup_transformers(self) -> None:
        self.add_transformer(self._normalize_timestamp)
        self.add_transformer(self._normalize_prices)
        self.add_transformer(self._normalize_volume)
        self.add_transformer(self._add_derived_metrics)

    def can_handle(self, data: Any) -> bool:
        if not isinstance(data, dict):
            return False

        keys = set(data.keys())
        price_fields = {"open", "high", "low", "close"}
        timestamp_fields = {"timestamp", "time", "t", "openTime", "open_time", "open_time_ms"}
        volume_fields = {"volume", "quote_volume", "base_volume", "amount", "quoteVolume"}

        # Avoid false positives on indicator payloads that only carry "timestamp".
        has_price_structure = len(price_fields & keys) >= 2
        has_time_or_volume = bool(timestamp_fields & keys) or bool(volume_fields & keys)
        return has_price_structure and has_time_or_volume

    def normalize(self, data: dict[str, Any]) -> NormalizationResult:
        original = dict(data) if self.config.preserve_original else None
        errors = []
        warnings = []

        try:
            # Validar
            validation_errors = self._apply_validators(data)
            if validation_errors and self.config.strict_mode:
                return NormalizationResult(
                    data={}, success=False, errors=validation_errors, original_data=original
                )
            warnings.extend(validation_errors)

            # Transformar
            normalized = self._apply_transformers(dict(data))

            # Agregar metadatos
            metadata = {}
            if self.config.add_metadata:
                metadata = {
                    "normalized_at": datetime.now(timezone.utc).isoformat(),
                    "normalizer": "OHLCVNormalizer",
                    "fields_present": list(normalized.keys()),
                }

            return NormalizationResult(
                data=normalized,
                success=True,
                errors=errors,
                warnings=warnings,
                metadata=metadata,
                original_data=original,
            )

        except Exception as e:
            logger.error(f"Error normalizando OHLCV: {e}")
            return NormalizationResult(
                data={}, success=False, errors=[str(e)], warnings=warnings, original_data=original
            )

    def _validate_required_fields(self, data: dict[str, Any]) -> bool:
        missing = self.REQUIRED_FIELDS - set(data.keys())
        if missing and self.config.strict_mode:
            raise ValidationError(f"Campos requeridos faltantes: {missing}")
        return len(missing) == 0 or not self.config.strict_mode

    def _validate_price_consistency(self, data: dict[str, Any]) -> bool:
        try:
            high = float(data.get("high", 0))
            low = float(data.get("low", float("inf")))
            open_p = float(data.get("open", 0))
            close = float(data.get("close", 0))

            if high < low:
                raise ValidationError("High < Low")
            if open_p < 0 or close < 0:
                raise ValidationError("Precios negativos")
            return True
        except (ValueError, TypeError) as e:
            raise ValidationError(f"Error en precios: {e}")

    def _validate_volume_positive(self, data: dict[str, Any]) -> bool:
        try:
            vol = float(data.get("volume", 0))
            if vol < 0:
                raise ValidationError("Volumen negativo")
            return True
        except (ValueError, TypeError) as e:
            raise ValidationError(f"Error en volumen: {e}")

    def _normalize_timestamp(self, data: dict[str, Any]) -> dict[str, Any]:
        """Normaliza timestamp a formato estándar."""
        ts = data.get("timestamp", data.get("time", data.get("t", 0)))

        if isinstance(ts, (int, float)):
            # Determinar si es ms o s
            if ts > 1e12:  # Milisegundos
                ts_ms = int(ts)
            else:  # Segundos
                ts_ms = int(ts * 1000)
            data["timestamp"] = ts_ms
            data["datetime"] = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()
        elif isinstance(ts, str):
            # Parsear ISO
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                data["timestamp"] = int(dt.timestamp() * 1000)
                data["datetime"] = dt.isoformat()
            except ValueError:
                logger.warning(f"No se pudo parsear timestamp: {ts}")

        return data

    def _normalize_prices(self, data: dict[str, Any]) -> dict[str, Any]:
        """Normaliza precios a Decimal con precisión configurada."""
        price_fields = ["open", "high", "low", "close", "vwap", "weighted_average"]

        for field in price_fields:
            if field in data:
                try:
                    val = Decimal(str(data[field]))
                    quantized = val.quantize(
                        Decimal(f"0.{'0' * self.config.decimal_precision}"), rounding=ROUND_HALF_UP
                    )
                    data[field] = float(quantized)
                except Exception as e:
                    logger.warning(f"No se pudo normalizar {field}: {e}")

        return data

    def _normalize_volume(self, data: dict[str, Any]) -> dict[str, Any]:
        """Normaliza volumen."""
        vol_fields = ["volume", "quote_volume", "base_volume", "amount"]

        for field in vol_fields:
            if field in data:
                try:
                    val = Decimal(str(data[field]))
                    quantized = val.quantize(
                        Decimal(f"0.{'0' * self.config.volume_precision}"), rounding=ROUND_HALF_UP
                    )
                    data[field] = float(quantized)
                except Exception as e:
                    logger.warning(f"No se pudo normalizar {field}: {e}")

        return data

    def _add_derived_metrics(self, data: dict[str, Any]) -> dict[str, Any]:
        """Agrega métricas derivadas útiles."""
        try:
            open_p = float(data.get("open", 0))
            close = float(data.get("close", 0))
            high = float(data.get("high", 0))
            low = float(data.get("low", 0))

            if open_p > 0:
                data["price_change"] = close - open_p
                data["price_change_pct"] = ((close - open_p) / open_p) * 100

            if high != low:
                data["candle_range"] = high - low
                data["candle_body"] = abs(close - open_p)
                data["upper_wick"] = high - max(open_p, close)
                data["lower_wick"] = min(open_p, close) - low
        except Exception as e:
            logger.warning(f"No se pudieron calcular métricas derivadas: {e}")

        return data


class IndicatorNormalizer(NormalizerBase[dict[str, Any]]):
    """Normalizador para indicadores técnicos."""

    # Rangos típicos por tipo de indicador
    KNOWN_RANGES = {
        "rsi": (0, 100),
        "stochastic": (0, 100),
        "williams_r": (-100, 0),
        "cci": (-300, 300),
        "momentum": (-100, 100),
        "adx": (0, 100),
        "atr": (0, float("inf")),
        "volume": (0, float("inf")),
    }

    def __init__(self, config: NormalizationConfig | None = None):
        super().__init__(config)
        self._setup_transformers()

    def _setup_transformers(self) -> None:
        self.add_transformer(self._normalize_values)
        self.add_transformer(self._detect_indicator_type)
        self.add_transformer(self._add_signals)

    def can_handle(self, data: Any) -> bool:
        if not isinstance(data, dict):
            return False

        keys = set(data.keys())
        direct_keys = {
            "value",
            "rsi",
            "macd",
            "macd_line",
            "signal_line",
            "histogram",
            "signal",
            "ema",
            "sma",
            "wma",
            "vwma",
            "hma",
            "bb",
            "bb_upper",
            "bb_middle",
            "bb_lower",
            "upper_band",
            "middle_band",
            "lower_band",
            "bandwidth",
            "atr",
            "adx",
            "stochastic",
            "stoch_k",
            "stoch_d",
            "cci",
            "williams_r",
            "williamsr",
            "wpr",
            "obv",
            "cmf",
            "mfi",
            "vwap",
            "vroc",
            "pvi",
            "nvi",
        }
        if keys & direct_keys:
            return True

        prefixes = ("ema_", "sma_", "wma_", "vwma_", "hma_", "bb_", "macd_", "stoch_")
        return any(any(key.startswith(prefix) for prefix in prefixes) for key in keys)

    def normalize(self, data: dict[str, Any]) -> NormalizationResult:
        original = dict(data) if self.config.preserve_original else None

        try:
            normalized = self._apply_transformers(dict(data))

            metadata = {}
            if self.config.add_metadata:
                metadata = {
                    "normalized_at": datetime.now(timezone.utc).isoformat(),
                    "normalizer": "IndicatorNormalizer",
                    "indicator_type": normalized.get("indicator_type", "unknown"),
                }

            return NormalizationResult(
                data=normalized, success=True, metadata=metadata, original_data=original
            )

        except Exception as e:
            return NormalizationResult(
                data={}, success=False, errors=[str(e)], original_data=original
            )

    def _normalize_values(self, data: dict[str, Any]) -> dict[str, Any]:
        """Normaliza valores numéricos del indicador."""
        for key, value in list(data.items()):
            if isinstance(value, (int, float, Decimal)):
                try:
                    data[key] = float(
                        Decimal(str(value)).quantize(
                            Decimal(f"0.{'0' * self.config.decimal_precision}"),
                            rounding=ROUND_HALF_UP,
                        )
                    )
                except:
                    pass
        return data

    def _detect_indicator_type(self, data: dict[str, Any]) -> dict[str, Any]:
        """Detecta el tipo de indicador basado en los campos presentes."""
        indicator_type = "unknown"
        data_keys = set(data.keys())

        # RSI detection
        if "rsi" in data_keys:
            indicator_type = "rsi"
        elif "value" in data_keys:
            val = data.get("value", -1)
            if isinstance(val, (int, float)) and 0 <= val <= 100:
                indicator_type = "rsi"

        # MACD detection
        macd_keys = {"macd", "macd_line", "signal_line", "histogram", "signal"}
        if data_keys & macd_keys:
            indicator_type = "macd"

        # Bollinger Bands detection
        bb_keys = {
            "bb_upper",
            "bb_middle",
            "bb_lower",
            "bandwidth",
            "bb_percent_b",
            "upper_band",
            "middle_band",
            "lower_band",
        }
        if data_keys & bb_keys:
            indicator_type = "bollinger_bands"

        # Moving Averages detection
        ma_keys = {"ema", "sma", "wma", "vwma", "hma"}
        if data_keys & ma_keys:
            indicator_type = "moving_average"
        else:
            # Check for prefixed MA fields like ema_20, sma_50, etc.
            for key in data_keys:
                if any(
                    key.startswith(prefix) for prefix in ["ema_", "sma_", "wma_", "vwma_", "hma_"]
                ):
                    indicator_type = "moving_average"
                    break

        # ATR detection
        if "atr" in data_keys:
            indicator_type = "atr"

        # ADX detection
        if "adx" in data_keys:
            indicator_type = "adx"

        # Stochastic detection
        stoch_keys = {"stochastic", "stoch_k", "stoch_d", "slowk", "slowd", "fastk", "fastd"}
        if data_keys & stoch_keys:
            indicator_type = "stochastic"

        # CCI detection
        if "cci" in data_keys:
            indicator_type = "cci"

        # Williams %R detection
        williams_keys = {"williams_r", "williamsr", "wpr"}
        if data_keys & williams_keys:
            indicator_type = "williams_r"

        # Volume indicators
        vol_keys = {"obv", "cmf", "mfi", "vwap", "vroc", "pvi", "nvi"}
        if data_keys & vol_keys:
            indicator_type = "volume"

        data["indicator_type"] = indicator_type
        return data

    def _add_signals(self, data: dict[str, Any]) -> dict[str, Any]:
        """Agrega señales interpretativas basadas en el tipo de indicador."""
        indicator_type = data.get("indicator_type", "unknown")

        if indicator_type == "rsi":
            value = data.get("rsi", data.get("value", 50))
            if value > 70:
                data["signal"] = "overbought"
                data["suggestion"] = "SELL"
            elif value < 30:
                data["signal"] = "oversold"
                data["suggestion"] = "BUY"
            else:
                data["signal"] = "neutral"
                data["suggestion"] = "HOLD"

        elif indicator_type == "macd":
            macd = data.get("macd", data.get("value", 0))
            signal = data.get("signal", data.get("signal_line", 0))
            histogram = data.get("histogram", macd - signal)

            if histogram > 0 and histogram > (
                data.get("prev_histogram", 0) if "prev_histogram" in data else 0
            ):
                data["signal"] = "bullish_crossover"
                data["suggestion"] = "BUY"
            elif histogram < 0 and histogram < (
                data.get("prev_histogram", 0) if "prev_histogram" in data else 0
            ):
                data["signal"] = "bearish_crossover"
                data["suggestion"] = "SELL"
            else:
                data["signal"] = "neutral"
                data["suggestion"] = "HOLD"

        return data


class SentimentNormalizer(NormalizerBase[dict[str, Any]]):
    """Normalizador para datos de sentimiento."""

    SENTIMENT_MAPPINGS = {
        "positive": 1,
        "bullish": 1,
        "optimistic": 1,
        "good": 1,
        "strong": 1,
        "negative": -1,
        "bearish": -1,
        "pessimistic": -1,
        "bad": -1,
        "weak": -1,
        "neutral": 0,
        "mixed": 0,
        "uncertain": 0,
        "flat": 0,
    }

    def __init__(self, config: NormalizationConfig | None = None):
        super().__init__(config)

    def can_handle(self, data: Any) -> bool:
        return isinstance(data, dict) and any(
            k in data for k in ["sentiment", "score", "polarity", "label", "emotion"]
        )

    def normalize(self, data: dict[str, Any]) -> NormalizationResult:
        original = dict(data) if self.config.preserve_original else None

        try:
            normalized = dict(data)

            # Normalizar score a rango [-1, 1]
            score = normalized.get(
                "sentiment", normalized.get("score", normalized.get("polarity", 0))
            )

            if isinstance(score, str):
                score_lower = score.lower()
                normalized_score = self.SENTIMENT_MAPPINGS.get(score_lower, 0)
            else:
                normalized_score = float(score)
                # Normalizar a [-1, 1] si está en otra escala
                if abs(normalized_score) > 1:
                    if normalized_score > 0:
                        normalized_score = min(normalized_score / 100, 1.0)
                    else:
                        normalized_score = max(normalized_score / 100, -1.0)

            normalized["normalized_sentiment"] = max(-1.0, min(1.0, normalized_score))

            # Determinar label si no existe
            if "label" not in normalized:
                if normalized_score > 0.1:
                    normalized["label"] = "positive"
                elif normalized_score < -0.1:
                    normalized["label"] = "negative"
                else:
                    normalized["label"] = "neutral"

            # Calcular magnitud/confianza
            normalized["magnitude"] = abs(normalized_score)

            if self.config.add_metadata:
                normalized["_metadata"] = {
                    "normalized_at": datetime.now(timezone.utc).isoformat(),
                    "normalizer": "SentimentNormalizer",
                }

            return NormalizationResult(data=normalized, success=True, original_data=original)

        except Exception as e:
            return NormalizationResult(
                data={}, success=False, errors=[str(e)], original_data=original
            )


class DataNormalizer:
    """Orquestador principal del sistema de normalización."""

    def __init__(self, config: NormalizationConfig | None = None):
        self.config = config or NormalizationConfig()
        self._normalizers: dict[DataType, list[NormalizerBase]] = {}
        self._auto_detect_normalizers: list[NormalizerBase] = []
        self._register_default_normalizers()

    def _register_default_normalizers(self) -> None:
        """Registra los normalizadores por defecto."""
        # Normalizadores específicos por tipo
        self.register_normalizer(DataType.MARKET_OHLCV, OHLCVNormalizer(self.config))
        self.register_normalizer(DataType.INDICATOR, IndicatorNormalizer(self.config))
        self.register_normalizer(DataType.SENTIMENT, SentimentNormalizer(self.config))

        # Normalizadores para auto-detección
        self._auto_detect_normalizers = [
            OHLCVNormalizer(self.config),
            IndicatorNormalizer(self.config),
            SentimentNormalizer(self.config),
        ]

    def register_normalizer(self, data_type: DataType, normalizer: NormalizerBase) -> None:
        """Registra un normalizador para un tipo de datos específico."""
        if data_type not in self._normalizers:
            self._normalizers[data_type] = []
        self._normalizers[data_type].append(normalizer)

    def normalize(self, data: Any, data_type: DataType | None = None) -> NormalizationResult:
        """
        Normaliza datos según el tipo especificado o auto-detectado.

        Args:
            data: Datos a normalizar
            data_type: Tipo de datos (si es None, se auto-detecta)

        Returns:
            NormalizationResult con los datos normalizados
        """
        if data is None:
            return NormalizationResult(data={}, success=False, errors=["Datos nulos"])

        # Si se especificó tipo, usar normalizadores registrados
        if data_type and data_type in self._normalizers:
            for normalizer in self._normalizers[data_type]:
                if normalizer.can_handle(data):
                    return normalizer.normalize(data)

        # Auto-detección
        for normalizer in self._auto_detect_normalizers:
            if normalizer.can_handle(data):
                logger.debug(f"Auto-detectado normalizador: {type(normalizer).__name__}")
                return normalizer.normalize(data)

        # No se encontró normalizador
        return NormalizationResult(
            data={"_raw": data},
            success=False,
            errors=["No se encontró normalizador compatible"],
            warnings=["Datos devueltos sin normalizar en '_raw'"],
        )

    def normalize_batch(
        self, data_list: list[Any], data_type: DataType | None = None
    ) -> list[NormalizationResult]:
        """Normaliza una lista de datos."""
        return [self.normalize(item, data_type) for item in data_list]

    def detect_data_type(self, data: Any) -> DataType | None:
        """Detecta el tipo de datos basado en su estructura."""
        for normalizer in self._auto_detect_normalizers:
            if normalizer.can_handle(data):
                if isinstance(normalizer, OHLCVNormalizer):
                    return DataType.MARKET_OHLCV
                elif isinstance(normalizer, IndicatorNormalizer):
                    return DataType.INDICATOR
                elif isinstance(normalizer, SentimentNormalizer):
                    return DataType.SENTIMENT
        return None


# Instancia global para uso conveniente
default_normalizer = DataNormalizer()


def normalize(
    data: Any, data_type: DataType | None = None, config: NormalizationConfig | None = None
) -> NormalizationResult:
    """Función de conveniencia para normalizar datos."""
    if config:
        normalizer = DataNormalizer(config)
        return normalizer.normalize(data, data_type)
    return default_normalizer.normalize(data, data_type)


def normalize_ohlcv(
    data: dict[str, Any], config: NormalizationConfig | None = None
) -> NormalizationResult:
    """Normaliza datos OHLCV."""
    return normalize(data, DataType.MARKET_OHLCV, config)


def normalize_indicator(
    data: dict[str, Any], config: NormalizationConfig | None = None
) -> NormalizationResult:
    """Normaliza datos de indicadores."""
    return normalize(data, DataType.INDICATOR, config)


def normalize_sentiment(
    data: dict[str, Any], config: NormalizationConfig | None = None
) -> NormalizationResult:
    """Normaliza datos de sentimiento."""
    return normalize(data, DataType.SENTIMENT, config)
