"""
Sistema Avanzado de Gestión de Riesgo para Fenix Trading Bot
Stop-loss dinámico con ATR y trailing stops inteligentes
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class RiskParameters:
    """Parámetros de riesgo calculados dinámicamente"""

    max_risk_per_trade: float  # Porcentaje del portfolio
    atr_sl_multiplier: float  # Multiplicador de ATR para stop-loss
    min_reward_risk_ratio: float
    trailing_stop_distance: float
    position_size: float
    max_drawdown_allowed: float
    time_stop_hours: int
    volatility_adjustment: float


@dataclass
class TradeRiskProfile:
    """Perfil de riesgo para una operación específica"""

    entry_price: float
    stop_loss_price: float
    take_profit_price: float
    position_size: float
    risk_amount: float
    reward_amount: float
    risk_reward_ratio: float
    atr_value: float
    volatility_factor: float
    confidence_score: float


@dataclass
class DynamicStopLoss:
    """Stop-loss dinámico que se ajusta según condiciones de mercado"""

    initial_sl: float
    current_sl: float
    initial_stop: float  # Alias para initial_sl para compatibilidad con tests
    trailing_distance: float  # Distancia de trailing stop
    atr_value: float
    volatility_factor: float
    time_factor: float
    trailing_active: bool
    last_update: datetime
    adjustment_count: int

    def __post_init__(self):
        if not hasattr(self, "initial_stop"):
            self.initial_stop = self.initial_sl
        if not hasattr(self, "trailing_distance"):
            self.trailing_distance = self.atr_value * 1.5


class AdvancedRiskManager:
    """Gestor avanzado de riesgo con stops dinámicos y trailing inteligente"""

    def __init__(self):
        self.default_atr_multiplier = 2.0
        self.min_atr_multiplier = 1.5
        self.max_atr_multiplier = 4.0
        self.volatility_threshold_low = 0.02
        self.volatility_threshold_high = 0.08
        self.max_portfolio_risk = 0.02  # 2% máximo por trade
        self.min_risk_reward = 1.5

    async def calculate_dynamic_risk_parameters(
        self, market_data: dict[str, Any], portfolio_value: float, confidence: float = 75.0
    ) -> RiskParameters:
        """Calcular parámetros de riesgo dinámicamente"""

        # Extraer datos de ATR y volatilidad
        atr_data = self._extract_atr_data(market_data)
        volatility = self._calculate_market_volatility(market_data)

        # Ajustar multiplicador ATR según volatilidad
        atr_multiplier = self._adjust_atr_multiplier(volatility)

        # Calcular riesgo basado en volatilidad y confianza
        risk_per_trade = self._calculate_risk_percentage(volatility, confidence)

        # Ajustar por tamaño de posición
        position_size = self._calculate_position_size(
            portfolio_value, risk_per_trade, atr_data["current_atr"]
        )

        # Calcular trailing stop distance
        trailing_distance = self._calculate_trailing_distance(atr_data["current_atr"], volatility)

        # Calcular time stop
        time_stop_hours = self._calculate_time_stop(volatility)

        # Ajuste de volatilidad para stops
        volatility_adjustment = self._calculate_volatility_adjustment(volatility)

        return RiskParameters(
            max_risk_per_trade=risk_per_trade,
            atr_sl_multiplier=atr_multiplier,
            min_reward_risk_ratio=self.min_risk_reward,
            trailing_stop_distance=trailing_distance,
            position_size=position_size,
            max_drawdown_allowed=0.05,  # 5% máximo drawdown
            time_stop_hours=time_stop_hours,
            volatility_adjustment=volatility_adjustment,
        )

    async def calculate_trade_risk_profile(
        self, entry_price: float, market_data: dict[str, Any], risk_params: RiskParameters
    ) -> TradeRiskProfile:
        """Calcular perfil completo de riesgo para una operación"""

        # Calcular stop-loss basado en ATR
        atr_value = self._get_current_atr(market_data)
        stop_loss_distance = atr_value * risk_params.atr_sl_multiplier

        # Determinar dirección de la operación (asumimos long por defecto)
        stop_loss_price = entry_price - stop_loss_distance

        # Calcular take-profit con ratio riesgo/recompensa mínimo
        risk_amount = entry_price - stop_loss_price
        reward_amount = risk_amount * risk_params.min_reward_risk_ratio
        take_profit_price = entry_price + reward_amount

        # Ajustar por volatilidad
        volatility_factor = self._calculate_volatility_factor(market_data)
        adjusted_stop = self._adjust_stop_for_volatility(
            stop_loss_price, volatility_factor, entry_price
        )
        adjusted_tp = self._adjust_tp_for_volatility(take_profit_price, volatility_factor)

        # Calcular score de confianza
        confidence = self._calculate_confidence_score(
            market_data, risk_params, entry_price, adjusted_stop
        )

        return TradeRiskProfile(
            entry_price=entry_price,
            stop_loss_price=adjusted_stop,
            take_profit_price=adjusted_tp,
            position_size=risk_params.position_size,
            risk_amount=abs(entry_price - adjusted_stop),
            reward_amount=abs(adjusted_tp - entry_price),
            risk_reward_ratio=abs(adjusted_tp - entry_price) / abs(entry_price - adjusted_stop),
            atr_value=atr_value,
            volatility_factor=volatility_factor,
            confidence_score=confidence,
        )

    async def create_dynamic_stop_loss(
        self, entry_price: float, market_data: dict[str, Any], risk_profile: TradeRiskProfile
    ) -> DynamicStopLoss:
        """Crear stop-loss dinámico que se ajusta según mercado"""

        atr_value = risk_profile.atr_value
        initial_sl = risk_profile.stop_loss_price

        # Factores de ajuste
        volatility_factor = self._calculate_volatility_factor(market_data)
        time_factor = 1.0  # Comenzar neutral

        trailing_distance = atr_value * 1.5
        return DynamicStopLoss(
            initial_sl=initial_sl,
            current_sl=initial_sl,
            initial_stop=initial_sl,
            trailing_distance=trailing_distance,
            atr_value=atr_value,
            volatility_factor=volatility_factor,
            time_factor=time_factor,
            trailing_active=False,
            last_update=datetime.now(),
            adjustment_count=0,
        )

    async def update_dynamic_stop(
        self, current_price: float, dynamic_sl: DynamicStopLoss, market_data: dict[str, Any]
    ) -> DynamicStopLoss:
        """Actualizar stop-loss dinámico según condiciones actuales"""

        # Actualizar factores de mercado
        new_volatility = self._calculate_market_volatility(market_data)
        new_atr = self._get_current_atr(market_data)

        # Calcular nuevo stop-loss
        new_sl = self._calculate_adjusted_stop(
            dynamic_sl.initial_sl, current_price, new_atr, new_volatility, dynamic_sl
        )

        # Verificar si activar trailing stop
        should_trail = self._should_activate_trailing(
            current_price, dynamic_sl.initial_sl, new_volatility
        )

        # Actualizar trailing si está activo
        if dynamic_sl.trailing_active or should_trail:
            new_sl = max(new_sl, current_price - (new_atr * 1.5))

        return DynamicStopLoss(
            initial_sl=dynamic_sl.initial_sl,
            current_sl=new_sl,
            atr_value=new_atr,
            volatility_factor=new_volatility,
            time_factor=dynamic_sl.time_factor * 0.99,  # Decrementar ligeramente con tiempo
            trailing_active=dynamic_sl.trailing_active or should_trail,
            last_update=datetime.now(),
            adjustment_count=dynamic_sl.adjustment_count + 1,
        )

    async def calculate_trailing_stop_levels(
        self,
        current_price: float,
        peak_price: float,
        atr_value: float,
        risk_profile: TradeRiskProfile,
    ) -> dict[str, float]:
        """Calcular niveles de trailing stop inteligente"""

        # Niveles de trailing basados en ATR y volatilidad
        tight_distance = atr_value * 1.0  # Muy ajustado
        medium_distance = atr_value * 1.5  # Balanceado
        wide_distance = atr_value * 2.5  # Conservador

        # Ajustar según volatilidad
        volatility_adj = risk_profile.volatility_factor
        tight_distance *= volatility_adj
        medium_distance *= volatility_adj
        wide_distance *= volatility_adj

        # Calcular stops
        tight_stop = peak_price - tight_distance
        medium_stop = peak_price - medium_distance
        wide_stop = peak_price - wide_distance

        return {
            "tight_stop": tight_stop,
            "medium_stop": medium_stop,
            "wide_stop": wide_stop,
            "recommended_distance": medium_distance,
            "breakeven_level": risk_profile.entry_price,
            "profit_lock_level": peak_price - (medium_distance * 0.5),
        }

    def _extract_atr_data(self, market_data: dict[str, Any]) -> dict[str, float]:
        """Extraer datos ATR del mercado"""
        atr_values = []

        # Buscar ATR en diferentes temporalidades
        for tf in ["1h", "4h", "1d"]:
            atr_key = f"atr_{tf}"
            if atr_key in market_data:
                atr_values.append(market_data[atr_key])

        # Usar ATR de 1h por defecto si no hay datos
        current_atr = atr_values[0] if atr_values else 0.02

        return {
            "current_atr": current_atr,
            "avg_atr": sum(atr_values) / len(atr_values) if atr_values else current_atr,
        }

    def _calculate_market_volatility(self, market_data: dict[str, Any]) -> float:
        """Calcular volatilidad actual del mercado"""

        # Buscar indicadores de volatilidad
        volatility_sources = []

        if "volatility_24h" in market_data:
            volatility_sources.append(market_data["volatility_24h"])

        if "price_change_24h" in market_data:
            price_change = abs(market_data["price_change_24h"])
            volatility_sources.append(price_change / 100)

        if "atr_1h" in market_data and "price" in market_data:
            atr_ratio = market_data["atr_1h"] / market_data["price"]
            volatility_sources.append(atr_ratio)

        # Valor por defecto si no hay datos
        if not volatility_sources:
            return 0.03  # 3% volatilidad por defecto

        return sum(volatility_sources) / len(volatility_sources)

    def _adjust_atr_multiplier(self, volatility: float) -> float:
        """Ajustar multiplicador ATR según volatilidad"""

        if volatility < self.volatility_threshold_low:
            return self.max_atr_multiplier  # Más amplio en mercados tranquilos
        elif volatility > self.volatility_threshold_high:
            return self.min_atr_multiplier  # Más ajustado en mercados volátiles
        else:
            # Interpolación lineal
            ratio = (volatility - self.volatility_threshold_low) / (
                self.volatility_threshold_high - self.volatility_threshold_low
            )
            return self.max_atr_multiplier - (
                ratio * (self.max_atr_multiplier - self.min_atr_multiplier)
            )

    def _calculate_risk_percentage(self, volatility: float, confidence: float) -> float:
        """Calcular porcentaje de riesgo por trade"""

        # Base: 2% del portfolio
        base_risk = 0.02

        # Ajustar por volatilidad
        if volatility > 0.05:  # Alta volatilidad
            risk_adj = base_risk * 0.7  # Reducir riesgo
        elif volatility < 0.01:  # Baja volatilidad
            risk_adj = base_risk * 1.3  # Aumentar riesgo
        else:
            risk_adj = base_risk

        # Ajustar por confianza
        confidence_factor = confidence / 100
        final_risk = risk_adj * (0.5 + confidence_factor)

        return min(final_risk, self.max_portfolio_risk)

    def _calculate_position_size(
        self, portfolio_value: float, risk_percentage: float, atr_value: float
    ) -> float:
        """Calcular tamaño de posición basado en riesgo"""

        # Tamaño basado en riesgo por ATR
        risk_amount = portfolio_value * risk_percentage
        stop_distance = atr_value * 2  # Asumir 2x ATR para cálculo

        position_size = risk_amount / stop_distance

        # Limitar a 20% del portfolio máximo
        max_position = portfolio_value * 0.2
        return min(position_size, max_position)

    def _calculate_trailing_distance(self, atr_value: float, volatility: float) -> float:
        """Calcular distancia óptima para trailing stop"""

        base_distance = atr_value * 1.5

        # Ajustar por volatilidad
        if volatility > 0.05:
            return base_distance * 1.2  # Más amplio en alta volatilidad
        elif volatility < 0.01:
            return base_distance * 0.8  # Más ajustado en baja volatilidad

        return base_distance

    def _calculate_time_stop(self, volatility: float) -> int:
        """Calcular time stop dinámico"""

        # Base: 24 horas
        base_hours = 24

        # Ajustar por volatilidad
        if volatility > 0.05:
            return int(base_hours * 0.7)  # Menos tiempo en alta volatilidad
        elif volatility < 0.01:
            return int(base_hours * 1.5)  # Más tiempo en baja volatilidad

        return base_hours

    def _get_current_atr(self, market_data: dict[str, Any]) -> float:
        """Obtener ATR actual"""
        atr_data = self._extract_atr_data(market_data)
        return atr_data["current_atr"]

    def _calculate_volatility_factor(self, market_data: dict[str, Any]) -> float:
        """Calcular factor de ajuste por volatilidad"""
        volatility = self._calculate_market_volatility(market_data)
        return 1.0 + (volatility - 0.03) * 10  # Factor alrededor de 1.0

    def _calculate_volatility_adjustment(self, volatility: float) -> float:
        """Ajuste adicional por volatilidad"""
        return max(0.5, min(2.0, volatility / 0.03))

    def _adjust_stop_for_volatility(
        self, stop_price: float, volatility_factor: float, entry_price: float
    ) -> float:
        """Ajustar stop-loss según factor de volatilidad"""
        adjustment = (volatility_factor - 1.0) * 0.02  # 2% base
        return stop_price * (1 - adjustment)

    def _adjust_tp_for_volatility(self, tp_price: float, volatility_factor: float) -> float:
        """Ajustar take-profit según volatilidad"""
        adjustment = (volatility_factor - 1.0) * 0.02
        return tp_price * (1 + adjustment)

    def _calculate_confidence_score(
        self,
        market_data: dict[str, Any],
        risk_params: RiskParameters,
        entry_price: float,
        stop_price: float,
    ) -> float:
        """Calcular score de confianza para la operación"""

        score = 50.0  # Base

        # Factor de volatilidad
        volatility = self._calculate_market_volatility(market_data)
        if 0.01 <= volatility <= 0.05:
            score += 20
        elif volatility > 0.08:
            score -= 15

        # Factor de ATR
        atr = self._get_current_atr(market_data)
        atr_ratio = abs(entry_price - stop_price) / entry_price
        if 0.005 <= atr_ratio <= 0.05:
            score += 15

        # Factor de liquidez (simulado)
        if "volume" in market_data and market_data["volume"] > 1000000:
            score += 10

        return min(max(score, 0), 100)

    def _calculate_adjusted_stop(
        self,
        initial_sl: float,
        current_price: float,
        new_atr: float,
        new_volatility: float,
        dynamic_sl: DynamicStopLoss,
    ) -> float:
        """Calcular nuevo stop-loss ajustado"""

        # Base: mantener distancia ATR
        base_distance = new_atr * dynamic_sl.atr_value

        # Ajustar por tiempo
        time_decay = max(0.9, dynamic_sl.time_factor)
        adjusted_distance = base_distance * time_decay

        # Calcular nuevo stop
        new_stop = current_price - adjusted_distance

        # No permitir que el stop se mueva en contra
        return max(new_stop, initial_sl)

    def _should_activate_trailing(
        self, current_price: float, initial_sl: float, volatility: float
    ) -> bool:
        """Determinar si activar trailing stop"""

        # Activar cuando el precio haya avanzado 1.5x ATR a favor
        profit_distance = abs(current_price - initial_sl)
        atr_based_threshold = volatility * 50  # Umbral dinámico

        return profit_distance > atr_based_threshold


# Instancia global
advanced_risk_manager = AdvancedRiskManager()
