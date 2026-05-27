# src/prompts/ab_testing.py
"""
Sistema de A/B Testing para Prompts de Fenix.

Permite experimentar con diferentes versiones de prompts y medir
su rendimiento en términos de:
- Precisión de señales
- Win rate
- Latencia de respuesta
- Calidad del razonamiento
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from src.prompts.agent_prompts import AgentType, PromptTemplate

logger = logging.getLogger(__name__)


@dataclass
class ExperimentMetrics:
    """Métricas de un experimento de prompt."""

    experiment_id: str
    variant: str
    agent_type: str

    # Contadores
    total_invocations: int = 0
    successful_parses: int = 0

    # Señales
    buy_signals: int = 0
    sell_signals: int = 0
    hold_signals: int = 0

    # Resultados (cuando se conocen)
    correct_signals: int = 0
    incorrect_signals: int = 0

    # Latencia
    total_latency_ms: float = 0.0
    min_latency_ms: float = float("inf")
    max_latency_ms: float = 0.0

    # Confianza
    high_confidence_count: int = 0
    medium_confidence_count: int = 0
    low_confidence_count: int = 0

    # Timestamps
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def avg_latency_ms(self) -> float:
        if self.total_invocations == 0:
            return 0.0
        return self.total_latency_ms / self.total_invocations

    @property
    def parse_success_rate(self) -> float:
        if self.total_invocations == 0:
            return 0.0
        return self.successful_parses / self.total_invocations

    @property
    def win_rate(self) -> float:
        total = self.correct_signals + self.incorrect_signals
        if total == 0:
            return 0.0
        return self.correct_signals / total


@dataclass
class PromptVariant:
    """Una variante de prompt para A/B testing."""

    name: str
    template: PromptTemplate
    weight: float = 1.0  # Peso para sampling
    is_control: bool = False  # True si es la variante de control

    def __hash__(self):
        return hash(self.name)


class PromptExperiment:
    """
    Experimento de A/B testing para un tipo de agente.

    Soporta:
    - Múltiples variantes de prompts
    - Weighted sampling
    - Métricas por variante
    - Persistencia de resultados
    """

    def __init__(
        self,
        experiment_id: str,
        agent_type: AgentType,
        storage_dir: Path | str = "data/experiments",
    ):
        self.experiment_id = experiment_id
        self.agent_type = agent_type
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        self.variants: dict[str, PromptVariant] = {}
        self.metrics: dict[str, ExperimentMetrics] = {}

        self._load_state()

    def add_variant(
        self,
        name: str,
        template: PromptTemplate,
        weight: float = 1.0,
        is_control: bool = False,
    ) -> None:
        """Añade una variante de prompt al experimento."""
        variant = PromptVariant(
            name=name,
            template=template,
            weight=weight,
            is_control=is_control,
        )
        self.variants[name] = variant

        if name not in self.metrics:
            self.metrics[name] = ExperimentMetrics(
                experiment_id=self.experiment_id,
                variant=name,
                agent_type=self.agent_type.value,
            )

        logger.info(f"Added variant '{name}' to experiment '{self.experiment_id}'")

    def select_variant(self, user_id: str | None = None) -> PromptVariant:
        """
        Selecciona una variante de prompt.

        Si se proporciona user_id, asegura consistencia (mismo usuario = misma variante).
        Si no, usa weighted random sampling.
        """
        if not self.variants:
            raise ValueError("No variants added to experiment")

        # Consistencia por usuario (SHA256 para seguridad)
        if user_id:
            hash_val = int(hashlib.sha256(user_id.encode()).hexdigest(), 16)
            variant_names = sorted(self.variants.keys())
            idx = hash_val % len(variant_names)
            return self.variants[variant_names[idx]]

        # Weighted random
        total_weight = sum(v.weight for v in self.variants.values())
        r = random.uniform(0, total_weight)

        cumulative = 0.0
        for variant in self.variants.values():
            cumulative += variant.weight
            if r <= cumulative:
                return variant

        # Fallback
        return list(self.variants.values())[0]

    def record_invocation(
        self,
        variant_name: str,
        latency_ms: float,
        parsed_ok: bool,
        signal: str | None = None,
        confidence: str | None = None,
    ) -> None:
        """Registra una invocación del prompt."""
        if variant_name not in self.metrics:
            return

        m = self.metrics[variant_name]
        m.total_invocations += 1

        if parsed_ok:
            m.successful_parses += 1

        # Latencia
        self._update_latency_metrics(m, latency_ms)

        # Señal
        if signal:
            self._update_signal_metrics(m, signal)

        # Confianza
        if confidence:
            self._update_confidence_metrics(m, confidence)

        m.updated_at = datetime.now().isoformat()
        self._save_state()

    def _update_latency_metrics(self, m: ExperimentMetrics, latency_ms: float) -> None:
        """Actualiza métricas de latencia."""
        m.total_latency_ms += latency_ms
        m.min_latency_ms = min(m.min_latency_ms, latency_ms)
        m.max_latency_ms = max(m.max_latency_ms, latency_ms)

    def _update_signal_metrics(self, m: ExperimentMetrics, signal: str) -> None:
        """Actualiza contadores de señales."""
        signal_upper = signal.upper()
        if signal_upper == "BUY":
            m.buy_signals += 1
        elif signal_upper == "SELL":
            m.sell_signals += 1
        else:
            m.hold_signals += 1

    def _update_confidence_metrics(self, m: ExperimentMetrics, confidence: str) -> None:
        """Actualiza contadores de confianza."""
        conf_upper = confidence.upper()
        if conf_upper == "HIGH":
            m.high_confidence_count += 1
        elif conf_upper == "MEDIUM":
            m.medium_confidence_count += 1
        else:
            m.low_confidence_count += 1

    def record_outcome(
        self,
        variant_name: str,
        was_correct: bool,
    ) -> None:
        """Registra el resultado de una señal (para calcular win rate)."""
        if variant_name not in self.metrics:
            return

        m = self.metrics[variant_name]
        if was_correct:
            m.correct_signals += 1
        else:
            m.incorrect_signals += 1

        m.updated_at = datetime.now().isoformat()
        self._save_state()

    def get_results(self) -> dict[str, Any]:
        """Retorna resultados comparativos del experimento."""
        results = {
            "experiment_id": self.experiment_id,
            "agent_type": self.agent_type.value,
            "variants": {},
        }

        for name, metrics in self.metrics.items():
            results["variants"][name] = {
                "total_invocations": metrics.total_invocations,
                "parse_success_rate": f"{metrics.parse_success_rate:.1%}",
                "avg_latency_ms": f"{metrics.avg_latency_ms:.1f}",
                "win_rate": f"{metrics.win_rate:.1%}",
                "signal_distribution": {
                    "buy": metrics.buy_signals,
                    "sell": metrics.sell_signals,
                    "hold": metrics.hold_signals,
                },
                "confidence_distribution": {
                    "high": metrics.high_confidence_count,
                    "medium": metrics.medium_confidence_count,
                    "low": metrics.low_confidence_count,
                },
                "is_control": self.variants.get(name, PromptVariant("", None)).is_control,
            }

        # Determinar ganador
        if len(self.metrics) >= 2:
            best_variant = max(
                self.metrics.items(), key=lambda x: (x[1].win_rate, -x[1].avg_latency_ms)
            )
            control_variant = next((v for v in self.variants.values() if v.is_control), None)

            results["winner"] = best_variant[0]
            if control_variant and best_variant[0] != control_variant.name:
                control_metrics = self.metrics.get(control_variant.name)
                if control_metrics:
                    lift = best_variant[1].win_rate - control_metrics.win_rate
                    results["lift_vs_control"] = f"{lift:+.1%}"

        return results

    def _get_state_file(self) -> Path:
        return self.storage_dir / f"{self.experiment_id}.json"

    def _save_state(self) -> None:
        """Persiste el estado del experimento."""
        state = {
            "experiment_id": self.experiment_id,
            "agent_type": self.agent_type.value,
            "metrics": {k: asdict(v) for k, v in self.metrics.items()},
        }

        with self._get_state_file().open("w") as f:
            json.dump(state, f, indent=2)

    def _load_state(self) -> None:
        """Carga el estado del experimento si existe."""
        state_file = self._get_state_file()
        if not state_file.exists():
            return

        try:
            with state_file.open() as f:
                state = json.load(f)

            for name, data in state.get("metrics", {}).items():
                self.metrics[name] = ExperimentMetrics(**data)

            logger.info(f"Loaded experiment state: {self.experiment_id}")
        except Exception as e:
            logger.warning(f"Failed to load experiment state: {e}")


class ABTestingManager:
    """
    Gestor global de experimentos de A/B testing.

    Uso:
    ```python
    manager = ABTestingManager()

    # Crear experimento
    exp = manager.create_experiment("technical_v2_test", AgentType.TECHNICAL)

    # Añadir variantes
    exp.add_variant("control", original_prompt, is_control=True)
    exp.add_variant("variant_a", new_prompt_a)
    exp.add_variant("variant_b", new_prompt_b)

    # En cada invocación
    variant = exp.select_variant()
    result = invoke_llm(variant.template.to_messages(**data))
    exp.record_invocation(variant.name, latency_ms, parsed_ok=True, signal="BUY")

    # Cuando se conoce el resultado
    exp.record_outcome(variant.name, was_correct=True)

    # Ver resultados
    print(exp.get_results())
    ```
    """

    def __init__(self, storage_dir: str = "data/ab_experiments"):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.experiments: dict[str, PromptExperiment] = {}

    def create_experiment(
        self,
        experiment_id: str,
        agent_type: AgentType,
    ) -> PromptExperiment:
        """Crea un nuevo experimento."""
        exp = PromptExperiment(
            experiment_id=experiment_id,
            agent_type=agent_type,
            storage_dir=self.storage_dir,
        )
        self.experiments[experiment_id] = exp
        return exp

    def get_experiment(self, experiment_id: str) -> PromptExperiment | None:
        """Obtiene un experimento por ID."""
        return self.experiments.get(experiment_id)

    def list_experiments(self) -> list[str]:
        """Lista todos los experimentos."""
        return list(self.experiments.keys())

    def get_all_results(self) -> dict[str, Any]:
        """Retorna resultados de todos los experimentos."""
        return {exp_id: exp.get_results() for exp_id, exp in self.experiments.items()}


# ============================================================================
# VARIANTES DE EJEMPLO PARA TECHNICAL AGENT
# ============================================================================

TECHNICAL_PROMPT_CONCISE = """Eres un analista técnico de cripto. Analiza indicadores y genera señales.

FORMATO JSON:
{
    "signal": "BUY|SELL|HOLD",
    "confidence_level": "HIGH|MEDIUM|LOW",
    "reasoning": "Breve explicación"
}

Indicadores clave: RSI (<30 sobreventa, >70 sobrecompra), MACD, EMA cruces, SuperTrend.
Responde SOLO con JSON válido."""

TECHNICAL_PROMPT_DETAILED = """Eres un analista técnico senior especializado en trading algorítmico de criptomonedas.
Tu análisis debe ser meticuloso y considerar múltiples factores antes de emitir una señal.

## METODOLOGÍA DE ANÁLISIS:
1. **Tendencia Principal**: Evalúa EMAs (9, 21, 50) y su alineación
2. **Momentum**: RSI, MACD histograma, y divergencias
3. **Volatilidad**: Bollinger Bands width, ATR relativo
4. **Confirmación**: SuperTrend, volumen, ADX

## CRITERIOS DE SEÑAL:
- BUY: Momentum positivo + tendencia alcista + confirmación de volumen
- SELL: Momentum negativo + tendencia bajista + confirmación de volumen  
- HOLD: Señales mixtas o falta de confirmación

## FORMATO DE RESPUESTA (JSON):
```json
{
    "signal": "BUY|SELL|HOLD",
    "confidence_level": "HIGH|MEDIUM|LOW",
    "reasoning": "Análisis detallado paso a paso...",
    "primary_factors": ["factor1", "factor2"],
    "risk_factors": ["riesgo1"]
}
```"""


# Singleton
_ab_manager: ABTestingManager | None = None


def get_ab_manager() -> ABTestingManager:
    """Obtiene el gestor de A/B testing singleton."""
    global _ab_manager
    if _ab_manager is None:
        _ab_manager = ABTestingManager()
    return _ab_manager
