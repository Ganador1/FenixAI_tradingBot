"""ReasoningBank: implementación inspirada en el paper arXiv:2509.25140.

El objetivo es almacenar los razonamientos generados por los agentes (sentiment,
technical, visual, etc.) para que puedan reusarse como memoria contextual y
para auditar cómo evoluciona la toma de decisiones. Sigue la filosofía del
paper ReasoningBank: cada razonamiento se clasifica como una "experiencia"
con metadatos ricos y se puede recuperar por palabras clave o por agente.
"""
from __future__ import annotations

import json
import threading
from collections import deque
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional

import hashlib
import logging
import math

try:  # sentence-transformers es opcional; fallback a Jaccard si no está disponible
    from sentence_transformers import SentenceTransformer
except Exception:  # pragma: no cover - fallback silencioso para entornos sin dependencia
    SentenceTransformer = None  # type: ignore

logger = logging.getLogger(__name__)


@dataclass
class ReasoningEntry:
    agent: str
    prompt_digest: str
    prompt: str
    reasoning: str
    action: str
    confidence: float
    backend: str
    latency_ms: Optional[float]
    metadata: Dict[str, Any]
    created_at: str
    embedding: Optional[List[float]] = None
    # Nuevos campos para self-judgment (paper ReasoningBank)
    success: Optional[bool] = None  # None=pendiente, True/False=evaluado
    reward: Optional[float] = None  # P&L del trade resultante
    reward_signal: Optional[float] = None  # Reward shaping normalizado (-2,2)
    near_miss: Optional[bool] = None
    reward_notes: Optional[str] = None
    evaluated_at: Optional[str] = None  # Timestamp de evaluación
    trade_id: Optional[str] = None  # Link al trade que resultó de esta decisión
    judge_verdict: Optional[str] = None
    judge_score: Optional[float] = None
    judge_confidence: Optional[float] = None
    judge_notes: Optional[str] = None
    judge_tags: List[str] = field(default_factory=list)
    judge_metadata: Dict[str, Any] = field(default_factory=dict)
    judge_success_estimate: Optional[bool] = None
    judged_at: Optional[str] = None

    def matches(self, query: str) -> bool:
        query_lower = query.lower()
        if query_lower in self.reasoning.lower():
            return True
        if query_lower in self.prompt.lower():
            return True
        if query_lower in self.metadata.get("tags", "").lower():
            return True
        return False
    
    def similarity_score(
        self,
        other_prompt: str,
        other_embedding: Optional[List[float]] = None,
    ) -> float:
        """Calcula similitud semántica si hay embeddings, Jaccard si no."""
        if other_embedding and self.embedding:
            return self._cosine_similarity(other_embedding)
        return self._keyword_overlap(other_prompt)

    def _keyword_overlap(self, other_prompt: str) -> float:
        this_words = set(self.prompt.lower().split())
        other_words = set(other_prompt.lower().split())
        if not this_words or not other_words:
            return 0.0
        intersection = this_words & other_words
        union = this_words | other_words
        return len(intersection) / len(union) if union else 0.0

    def _cosine_similarity(self, other_embedding: List[float]) -> float:
        if not self.embedding or not other_embedding:
            return 0.0
        dot_product = sum(a * b for a, b in zip(self.embedding, other_embedding))
        norm_self = math.sqrt(sum(a * a for a in self.embedding))
        norm_other = math.sqrt(sum(b * b for b in other_embedding))
        if norm_self == 0 or norm_other == 0:
            return 0.0
        return float(dot_product / (norm_self * norm_other))


class ReasoningBank:
    def __init__(
        self,
        storage_dir: str = "logs/reasoning_bank",
        max_entries_per_agent: int = 500,
        use_embeddings: bool = True,
        embedding_model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        embedding_device: Optional[str] = None,
        embedding_backend: Optional[Callable[[str], List[float]]] = None,
    ) -> None:
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.max_entries_per_agent = max_entries_per_agent
        self._embedding_backend = embedding_backend
        self.embedding_model_name = embedding_model_name
        self.embedding_device = embedding_device
        self.use_embeddings = bool(
            embedding_backend is not None or (use_embeddings and SentenceTransformer is not None)
        )
        self._embedding_model: Optional[Any] = None
        self._lock = threading.RLock()
        self._embedding_lock = threading.Lock()
        self._cache: Dict[str, Deque[ReasoningEntry]] = {}
        self._stats_path = self.storage_dir / "index.json"
        self._stats: Dict[str, Any] = {}
        self._embedding_warning_emitted = False
        self._load_stats()

    def _load_stats(self) -> None:
        if self._stats_path.exists():
            try:
                self._stats = json.loads(self._stats_path.read_text())
            except Exception as exc:
                logger.warning("ReasoningBank: no se pudo leer el index: %s", exc)
                self._stats = {}

    def _save_stats(self) -> None:
        try:
            self._stats_path.write_text(json.dumps(self._stats, indent=2))
        except Exception as exc:
            logger.debug("ReasoningBank: error guardando index: %s", exc)

    def _get_embedding_model(self) -> Optional[Any]:
        if not self.use_embeddings or self._embedding_backend is not None:
            return None
        if SentenceTransformer is None:
            if not self._embedding_warning_emitted:
                logger.info(
                    "ReasoningBank: sentence-transformers no disponible, usando similitud por palabras"
                )
                self._embedding_warning_emitted = True
            self.use_embeddings = False
            return None
        if self._embedding_model is None:
            with self._embedding_lock:
                if self._embedding_model is None:
                    try:
                        self._embedding_model = SentenceTransformer(
                            self.embedding_model_name,
                            device=self.embedding_device,
                        )
                    except Exception as exc:  # pragma: no cover - dependencias externas
                        logger.warning(
                            "ReasoningBank: no se pudo cargar el modelo %s (%s)",
                            self.embedding_model_name,
                            exc,
                        )
                        self.use_embeddings = False
                        return None
        return self._embedding_model

    def _embed_text(self, text: str) -> Optional[List[float]]:
        if not text or not self.use_embeddings:
            return None
        if self._embedding_backend is not None:
            try:
                vector = self._embedding_backend(text)
                return [float(x) for x in vector]
            except Exception as exc:  # pragma: no cover - backend inestable en tests
                if not self._embedding_warning_emitted:
                    logger.warning("ReasoningBank: backend de embeddings falló: %s", exc)
                    self._embedding_warning_emitted = True
                return None

        model = self._get_embedding_model()
        if model is None:
            return None
        try:
            vector = model.encode(text, normalize_embeddings=True)
            if hasattr(vector, "tolist"):
                vector = vector.tolist()
            return [float(x) for x in vector]
        except Exception as exc:  # pragma: no cover - dependencias externas
            if not self._embedding_warning_emitted:
                logger.warning(
                    "ReasoningBank: no se pudo generar embedding, fallback a Jaccard (%s)",
                    exc,
                )
                self._embedding_warning_emitted = True
            return None

    def _maybe_attach_embedding(self, entry: ReasoningEntry) -> None:
        embedding_text = f"{entry.prompt}\n{entry.reasoning}".strip()
        embedding = self._embed_text(embedding_text)
        if embedding is not None:
            entry.embedding = embedding

    def store_entry(
        self,
        agent_name: str,
        prompt: str,
        normalized_result: Dict[str, Any],
        raw_response: str,
        backend: str,
        latency_ms: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ReasoningEntry:
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
        action_value = str(normalized_result.get("action", "") or "").strip()
        if not action_value:
            action_value = str(
                normalized_result.get("final_decision")
                or normalized_result.get("signal")
                or normalized_result.get("decision")
                or "UNKNOWN"
            )
        confidence_value = normalized_result.get("confidence")
        if confidence_value is None:
            decision_conf = str(normalized_result.get("confidence_in_decision", "") or "").upper()
            confidence_map = {"LOW": 0.35, "MEDIUM": 0.55, "HIGH": 0.8}
            if decision_conf in confidence_map:
                confidence_value = confidence_map[decision_conf]
            else:
                # Otros alias comunes (confidence_score, confidence_level numérico, etc.)
                confidence_value = (
                    normalized_result.get("confidence_score")
                    or normalized_result.get("confidence_level")
                    or 0.5
                )
        reasoning_text = (
            normalized_result.get("reason")
            or normalized_result.get("reasoning")
            or normalized_result.get("combined_reasoning")
            or raw_response[:500]
        )

        try:
            confidence_value = float(confidence_value)
        except (TypeError, ValueError):
            confidence_value = 0.5

        # Create timestamp with timezone and human-readable format
        now = datetime.now()
        created_at_iso = now.astimezone().isoformat()  # Includes timezone
        analysis_timestamp = now.strftime("%Y-%m-%d %H:%M:%S %Z")  # Human readable
        
        entry = ReasoningEntry(
            agent=agent_name,
            prompt_digest=digest,
            prompt=prompt,
            reasoning=str(reasoning_text),
            action=str(action_value),
            confidence=confidence_value,
            backend=backend,
            latency_ms=latency_ms,
            metadata={
                **(metadata or {}),
                "analysis_timestamp": analysis_timestamp,
            },
            created_at=created_at_iso,
        )
        self._maybe_attach_embedding(entry)

        with self._lock:
            agent_cache = self._cache.setdefault(agent_name, deque(maxlen=self.max_entries_per_agent))
            agent_cache.append(entry)
            agent_file = self.storage_dir / f"{agent_name}.jsonl"
            with agent_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(entry)) + "\n")

            stats = self._stats.setdefault(agent_name, {"total": 0})
            stats["total"] = stats.get("total", 0) + 1
            stats["last_recorded"] = entry.created_at
            self._save_stats()

        return entry

    def get_recent(self, agent_name: str, limit: int = 5) -> List[ReasoningEntry]:
        with self._lock:
            agent_cache = self._cache.get(agent_name)
            if not agent_cache:
                agent_file = self.storage_dir / f"{agent_name}.jsonl"
                if agent_file.exists():
                    agent_cache = deque(maxlen=self.max_entries_per_agent)
                    with agent_file.open("r", encoding="utf-8") as fh:
                        for line in fh.readlines()[-self.max_entries_per_agent :]:
                            try:
                                agent_cache.append(ReasoningEntry(**json.loads(line)))
                            except Exception:
                                continue
                    self._cache[agent_name] = agent_cache
                else:
                    return []
            return list(agent_cache)[-limit:]

    def search(self, agent_name: str, query: str, limit: int = 5) -> List[ReasoningEntry]:
        entries = self.get_recent(agent_name, self.max_entries_per_agent)
        matches = [entry for entry in reversed(entries) if entry.matches(query)]
        return matches[:limit]
    
    def get_relevant_context(
        self,
        agent_name: str,
        current_prompt: str,
        limit: int = 3,
        min_similarity: float = 0.3,
        prefer_successful: bool = True
    ) -> List[ReasoningEntry]:
        """Recupera experiencias relevantes para inyectar en prompt (core del paper).
        
        Args:
            agent_name: Nombre del agente
            current_prompt: Prompt actual para el cual buscar contexto
            limit: Máximo número de entradas a devolver
            min_similarity: Umbral mínimo de similitud (0-1)
            prefer_successful: Si True, prioriza experiencias exitosas
        
        Returns:
            Lista de ReasoningEntry relevantes, ordenadas por relevancia
        """
        entries = self.get_recent(agent_name, self.max_entries_per_agent)
        if not entries:
            return []
        current_embedding: Optional[List[float]] = None
        if self.use_embeddings:
            current_embedding = self._embed_text(current_prompt)
            if current_embedding is None:
                logger.debug("ReasoningBank: fallback a similitud por palabras para %s", agent_name)
        
        # Calcular similitud y filtrar
        scored_entries = []
        for entry in entries:
            score = entry.similarity_score(current_prompt, current_embedding)
            if score >= min_similarity:
                # Boost para experiencias exitosas
                if prefer_successful and entry.success is True:
                    score *= 1.5
                scored_entries.append((score, entry))
        
        # Ordenar por score descendente
        scored_entries.sort(key=lambda x: x[0], reverse=True)
        
        return [entry for _, entry in scored_entries[:limit]]
    
    def update_entry_outcome(
        self,
        agent_name: str,
        prompt_digest: str,
        success: bool,
        reward: float,
        trade_id: Optional[str] = None,
        reward_signal: Optional[float] = None,
        near_miss: Optional[bool] = None,
        reward_notes: Optional[str] = None,
    ) -> bool:
        """Actualiza una entrada con el resultado real del trade (self-judgment).
        
        Args:
            agent_name: Nombre del agente
            prompt_digest: Digest del prompt a actualizar
            success: Si el trade fue exitoso
            reward: P&L del trade
            trade_id: ID del trade asociado
        
        Returns:
            True si se actualizó exitosamente
        """
        with self._lock:
            agent_cache = self._cache.get(agent_name)
            if not agent_cache:
                return False
            
            # Buscar y actualizar en cache
            updated = False
            for entry in agent_cache:
                if entry.prompt_digest == prompt_digest:
                    entry.success = success
                    entry.reward = reward
                    entry.evaluated_at = datetime.utcnow().isoformat()
                    entry.trade_id = trade_id
                    entry.reward_signal = reward_signal
                    entry.near_miss = near_miss
                    if reward_notes:
                        entry.reward_notes = reward_notes
                    updated = True
                    break
            
            if not updated:
                return False
            
            # Re-escribir archivo JSONL con datos actualizados
            if self._rewrite_agent_file(agent_name, agent_cache):
                logger.info(f"ReasoningBank: Updated entry {prompt_digest[:8]} for {agent_name}")
                return True
            return False

    def attach_judge_feedback(
        self,
        agent_name: str,
        prompt_digest: str,
        judge_payload: Dict[str, Any]
    ) -> bool:
        """Enlaza el veredicto del LLM-as-a-judge con la entrada de ReasoningBank."""
        if not judge_payload:
            return False

        with self._lock:
            agent_cache = self._cache.get(agent_name)
            if not agent_cache:
                # Intentar cargar desde disco si aún no está en caché
                self.get_recent(agent_name, self.max_entries_per_agent)
                agent_cache = self._cache.get(agent_name)
                if not agent_cache:
                    return False

            updated = False
            for entry in agent_cache:
                if entry.prompt_digest == prompt_digest:
                    entry.judge_verdict = judge_payload.get("verdict")
                    entry.judge_score = judge_payload.get("score")
                    entry.judge_confidence = judge_payload.get("confidence")
                    entry.judge_notes = judge_payload.get("notes")
                    entry.judge_tags = list(judge_payload.get("tags") or [])
                    entry.judge_metadata = judge_payload.get("metadata") or {}
                    entry.judge_success_estimate = judge_payload.get("success_estimate")
                    entry.judged_at = datetime.utcnow().isoformat()
                    updated = True
                    break

            if not updated:
                return False

            if self._rewrite_agent_file(agent_name, agent_cache):
                logger.info(
                    "ReasoningBank: Judge feedback attached to %s for %s",
                    prompt_digest[:8],
                    agent_name,
                )
                return True
            return False

    def _rewrite_agent_file(self, agent_name: str, agent_cache: Deque[ReasoningEntry]) -> bool:
        """Persiste todas las entradas del agente nuevamente en disco."""
        agent_file = self.storage_dir / f"{agent_name}.jsonl"
        try:
            with agent_file.open("w", encoding="utf-8") as fh:
                for entry in agent_cache:
                    fh.write(json.dumps(asdict(entry)) + "\n")
            return True
        except Exception as exc:
            logger.error(f"ReasoningBank: Failed to persist file for {agent_name}: {exc}")
            return False
    
    def get_success_rate(self, agent_name: str, lookback: int = 50) -> Dict[str, Any]:
        """Calcula tasa de éxito para experiencias evaluadas."""
        entries = self.get_recent(agent_name, lookback)
        evaluated = [e for e in entries if e.success is not None]
        
        if not evaluated:
            return {"total_evaluated": 0, "success_rate": 0.0, "avg_reward": 0.0}
        
        successful = [e for e in evaluated if e.success]
        total_reward = sum(e.reward or 0.0 for e in evaluated)
        
        return {
            "total_evaluated": len(evaluated),
            "successful": len(successful),
            "success_rate": len(successful) / len(evaluated),
            "avg_reward": total_reward / len(evaluated),
            "total_reward": total_reward
        }
    
    def extract_success_patterns(
        self,
        agent_name: str,
        min_confidence: float = 0.7
    ) -> Dict[str, Any]:
        """Análisis contrastivo: qué diferencia éxitos de fracasos (core del paper).
        
        Returns:
            Dict con patrones encontrados en experiencias exitosas vs fallidas
        """
        entries = self.get_recent(agent_name, self.max_entries_per_agent)
        evaluated = [e for e in entries if e.success is not None]
        
        if len(evaluated) < 10:  # Mínimo para análisis estadístico
            return {"status": "insufficient_data", "evaluated_count": len(evaluated)}
        
        successful = [e for e in evaluated if e.success]
        failed = [e for e in evaluated if not e.success]
        
        # Análisis de acciones
        success_actions = [e.action for e in successful]
        fail_actions = [e.action for e in failed]
        
        from collections import Counter
        success_action_counts = Counter(success_actions)
        fail_action_counts = Counter(fail_actions)
        
        # Análisis de confidence
        success_avg_conf = sum(e.confidence for e in successful) / len(successful) if successful else 0
        fail_avg_conf = sum(e.confidence for e in failed) / len(failed) if failed else 0
        
        # Patrones de alta confianza
        high_conf_success = [e for e in successful if e.confidence >= min_confidence]
        high_conf_fail = [e for e in failed if e.confidence >= min_confidence]
        
        return {
            "status": "analyzed",
            "total_evaluated": len(evaluated),
            "successful_count": len(successful),
            "failed_count": len(failed),
            "success_rate": len(successful) / len(evaluated),
            "avg_confidence": {
                "successful": success_avg_conf,
                "failed": fail_avg_conf,
                "delta": success_avg_conf - fail_avg_conf
            },
            "action_patterns": {
                "successful_actions": dict(success_action_counts.most_common(3)),
                "failed_actions": dict(fail_action_counts.most_common(3))
            },
            "high_confidence_outcomes": {
                "successful": len(high_conf_success),
                "failed": len(high_conf_fail),
                "accuracy_at_high_conf": (
                    len(high_conf_success) / (len(high_conf_success) + len(high_conf_fail))
                    if (len(high_conf_success) + len(high_conf_fail)) > 0 else 0
                )
            },
            "insights": self._generate_insights(
                successful, failed, min_confidence
            )
        }
    
    def _generate_insights(
        self,
        successful: List[ReasoningEntry],
        failed: List[ReasoningEntry],
        min_confidence: float
    ) -> List[str]:
        """Genera insights textuales del análisis contrastivo."""
        insights = []
        
        if not successful or not failed:
            return ["Insufficient data for contrastive analysis"]
        
        # Insight 1: Confidence threshold
        success_avg_conf = sum(e.confidence for e in successful) / len(successful)
        fail_avg_conf = sum(e.confidence for e in failed) / len(failed)
        
        if success_avg_conf > fail_avg_conf + 0.1:
            insights.append(
                f"Successful decisions have {success_avg_conf:.1%} avg confidence "
                f"vs {fail_avg_conf:.1%} for failed ones. Higher confidence correlates with success."
            )
        
        # Insight 2: Action distribution
        from collections import Counter
        success_actions = Counter(e.action for e in successful)
        best_action = success_actions.most_common(1)[0] if success_actions else None
        
        if best_action:
            action_name, action_count = best_action
            action_rate = action_count / len(successful)
            if action_rate > 0.4:
                insights.append(
                    f"Action '{action_name}' appears in {action_rate:.0%} of successful cases."
                )
        
        # Insight 3: Latency
        success_latencies = [e.latency_ms for e in successful if e.latency_ms]
        fail_latencies = [e.latency_ms for e in failed if e.latency_ms]
        
        if success_latencies and fail_latencies:
            avg_success_lat = sum(success_latencies) / len(success_latencies)
            avg_fail_lat = sum(fail_latencies) / len(fail_latencies)
            if abs(avg_success_lat - avg_fail_lat) > 500:  # >500ms difference
                insights.append(
                    f"Latency differs: {avg_success_lat:.0f}ms (success) vs {avg_fail_lat:.0f}ms (fail)"
                )
        
        return insights if insights else ["No significant patterns detected"]
    
    def synthesize_strategies(
        self,
        agent_name: str,
        min_success_rate: float = 0.65,
        min_sample_size: int = 10
    ) -> List[Dict[str, Any]]:
        """Sintetiza estrategias generalizables a partir de experiencias (paper ReasoningBank).
        
        Extrae patrones de alto nivel tipo "Cuando X, hacer Y resulta en Z".
        
        Args:
            agent_name: Nombre del agente
            min_success_rate: Tasa mínima de éxito para considerar una estrategia
            min_sample_size: Mínimo de casos para validar una estrategia
        
        Returns:
            Lista de estrategias sintetizadas con metadatos
        """
        entries = self.get_recent(agent_name, self.max_entries_per_agent)
        evaluated = [e for e in entries if e.success is not None]
        
        if len(evaluated) < min_sample_size:
            return []
        
        strategies = []
        
        # Estrategia 1: Por nivel de confidence
        confidence_buckets = {
            "high": [e for e in evaluated if e.confidence >= 0.8],
            "medium": [e for e in evaluated if 0.5 <= e.confidence < 0.8],
            "low": [e for e in evaluated if e.confidence < 0.5]
        }
        
        for bucket_name, bucket_entries in confidence_buckets.items():
            if len(bucket_entries) >= min_sample_size:
                success_count = sum(1 for e in bucket_entries if e.success)
                success_rate = success_count / len(bucket_entries)
                
                if success_rate >= min_success_rate:
                    avg_reward = sum(e.reward or 0 for e in bucket_entries if e.success) / max(success_count, 1)
                    strategies.append({
                        "type": "confidence_threshold",
                        "rule": f"When confidence is {bucket_name} (examples: {bucket_name})",
                        "condition": f"confidence {'≥ 0.8' if bucket_name == 'high' else '0.5-0.8' if bucket_name == 'medium' else '< 0.5'}",
                        "success_rate": success_rate,
                        "sample_size": len(bucket_entries),
                        "avg_reward": avg_reward,
                        "recommendation": self._generate_recommendation(bucket_name, success_rate, avg_reward)
                    })
        
        # Estrategia 2: Por acción específica
        from collections import defaultdict
        action_outcomes = defaultdict(list)
        for entry in evaluated:
            action_outcomes[entry.action].append(entry)
        
        for action, action_entries in action_outcomes.items():
            if len(action_entries) >= min_sample_size:
                success_count = sum(1 for e in action_entries if e.success)
                success_rate = success_count / len(action_entries)
                
                if success_rate >= min_success_rate:
                    avg_reward = sum(e.reward or 0 for e in action_entries if e.success) / max(success_count, 1)
                    avg_conf = sum(e.confidence for e in action_entries if e.success) / max(success_count, 1)
                    
                    strategies.append({
                        "type": "action_strategy",
                        "rule": f"Action '{action}' is effective",
                        "condition": f"action == {action}",
                        "success_rate": success_rate,
                        "sample_size": len(action_entries),
                        "avg_reward": avg_reward,
                        "avg_confidence": avg_conf,
                        "recommendation": f"Continue using {action} when conditions are similar. Success rate: {success_rate:.1%}"
                    })
        
        # Estrategia 3: Patrones temporales (latency)
        if any(e.latency_ms for e in evaluated):
            latency_sorted = sorted([e for e in evaluated if e.latency_ms], key=lambda x: x.latency_ms or 0)
            fast_entries = latency_sorted[:len(latency_sorted)//2]  # 50% más rápidos
            slow_entries = latency_sorted[len(latency_sorted)//2:]
            
            if len(fast_entries) >= min_sample_size:
                fast_success = sum(1 for e in fast_entries if e.success) / len(fast_entries)
                slow_success = sum(1 for e in slow_entries if e.success) / len(slow_entries)
                
                if abs(fast_success - slow_success) > 0.15:  # Diferencia significativa
                    better_group = "fast" if fast_success > slow_success else "slow"
                    strategies.append({
                        "type": "latency_pattern",
                        "rule": f"{better_group.capitalize()} responses tend to be more successful",
                        "condition": f"latency {'<' if better_group == 'fast' else '>'} {sum(e.latency_ms or 0 for e in latency_sorted) / len(latency_sorted):.0f}ms",
                        "success_rate": fast_success if better_group == "fast" else slow_success,
                        "sample_size": len(fast_entries),
                        "delta": abs(fast_success - slow_success),
                        "recommendation": f"Monitor inference latency. {better_group.capitalize()} responses show {abs(fast_success - slow_success):.1%} better success rate."
                    })
        
        # Ordenar por success_rate * sample_size (combina calidad y confianza estadística)
        strategies.sort(key=lambda s: s["success_rate"] * s["sample_size"], reverse=True)
        
        return strategies
    
    def _generate_recommendation(self, bucket_name: str, success_rate: float, avg_reward: float) -> str:
        """Genera recomendación basada en métricas."""
        if bucket_name == "high" and success_rate > 0.75:
            return f"STRONG BUY: High confidence trades show {success_rate:.1%} success with avg {avg_reward:+.2f}% reward"
        elif bucket_name == "medium" and success_rate > 0.6:
            return f"MODERATE: Medium confidence acceptable at {success_rate:.1%} success"
        elif success_rate < 0.5:
            return f"AVOID: {bucket_name.capitalize()} confidence shows poor {success_rate:.1%} success rate"
        else:
            return f"NEUTRAL: {bucket_name.capitalize()} confidence at {success_rate:.1%} success"

    def summarize_agent(self, agent_name: str) -> Dict[str, Any]:
        stats = self._stats.get(agent_name, {})
        recent = self.get_recent(agent_name, limit=3)
        success_stats = self.get_success_rate(agent_name, lookback=100)
        
        return {
            "agent": agent_name,
            "total_reasonings": stats.get("total", 0),
            "last_recorded": stats.get("last_recorded"),
            "recent_summaries": [entry.reasoning[:200] for entry in recent],
            "performance": success_stats,
            "has_evaluated_data": success_stats["total_evaluated"] > 0
        }


_reasoning_bank: Optional[ReasoningBank] = None
_reasoning_bank_lock = threading.Lock()


def get_reasoning_bank() -> ReasoningBank:
    """Get singleton ReasoningBank instance.
    
    Note: Embeddings disabled by default to prevent memory issues on macOS.
    Uses Jaccard similarity fallback instead.
    """
    global _reasoning_bank
    if _reasoning_bank is None:
        with _reasoning_bank_lock:
            if _reasoning_bank is None:
                # Disable embeddings to prevent memory issues with SentenceTransformer
                # Jaccard similarity is used as fallback (fast, no RAM overhead)
                _reasoning_bank = ReasoningBank(use_embeddings=False)
    return _reasoning_bank
