"""ReasoningBank con SQLite backend optimizado.

Reemplaza la re-escritura O(n) de archivos JSONL con SQLite + índices.
Mantiene la misma API pública para compatibilidad.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable

try:
    from sentence_transformers import SentenceTransformer
except Exception:
    SentenceTransformer = None

from src.memory.reasoning_bank import ReasoningEntry

logger = logging.getLogger(__name__)


class ReasoningBankOptimized:
    """
    ReasoningBank con backend SQLite optimizado.
    
    Mejoras:
    - SQLite + índices en lugar de re-escritura O(n)
    - Append-only para inserts (O(1))
    - Updates por digest (O(log n) con índice)
    - Búsquedas por similitud aceleradas
    """
    
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
        self._embedding_lock = threading.Lock()
        self._lock = threading.RLock()
        
        # SQLite path
        self.db_path = self.storage_dir / "reasoning_bank.db"
        
        # Inicializar schema
        self._init_db()
    
    def _init_db(self) -> None:
        """Inicializa el schema SQLite."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS reasoning_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent TEXT NOT NULL,
                    prompt_digest TEXT UNIQUE NOT NULL,
                    prompt TEXT NOT NULL,
                    reasoning TEXT,
                    action TEXT,
                    confidence REAL,
                    backend TEXT,
                    latency_ms REAL,
                    metadata TEXT,
                    created_at TEXT,
                    embedding TEXT,  -- JSON array como string
                    success INTEGER,  -- NULL pendiente, 0/1 evaluado
                    reward REAL,
                    reward_signal REAL,
                    near_miss INTEGER,
                    reward_notes TEXT,
                    evaluated_at TEXT,
                    trade_id TEXT,
                    judge_verdict TEXT,
                    judge_score REAL,
                    judge_confidence REAL,
                    judge_notes TEXT,
                    judge_tags TEXT,  -- JSON array
                    judge_metadata TEXT,
                    judge_success_estimate INTEGER,
                    judged_at TEXT
                )
            """)
            
            # Índices para queries comunes
            conn.execute("CREATE INDEX IF NOT EXISTS idx_agent ON reasoning_entries(agent)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_digest ON reasoning_entries(prompt_digest)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_created ON reasoning_entries(created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_created ON reasoning_entries(agent, created_at)")
    
    def _embed_text(self, text: str) -> Optional[List[float]]:
        """Genera embedding para el texto."""
        if not text or not self.use_embeddings:
            return None
        if self._embedding_backend is not None:
            try:
                vector = self._embedding_backend(text)
                return [float(x) for x in vector]
            except Exception as exc:
                logger.warning(f"Embedding backend failed: {exc}")
                return None
        
        # Cargar modelo SentenceTransformer si no está disponible
        if SentenceTransformer is None:
            return None
        
        with self._embedding_lock:
            if self._embedding_model is None:
                try:
                    self._embedding_model = SentenceTransformer(
                        self.embedding_model_name,
                        device=self.embedding_device,
                    )
                except Exception as exc:
                    logger.warning(f"Could not load embedding model: {exc}")
                    self.use_embeddings = False
                    return None
            
            try:
                vector = self._embedding_model.encode(text, normalize_embeddings=True)
                return vector.tolist() if hasattr(vector, "tolist") else list(vector)
            except Exception as exc:
                logger.warning(f"Could not generate embedding: {exc}")
                return None
    
    def store_entry(
        self,
        agent_name: str,
        prompt: str,
        normalized_result: Dict[str, Any],
        raw_response: str,
        backend: str,
        latency_ms: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "ReasoningEntryOptimized":
        """Almacena una entry (O(1) append-only)."""
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
        
        # Extraer campos del resultado
        action_value = str(normalized_result.get("action", "") or "")
        if not action_value:
            action_value = str(
                normalized_result.get("final_decision")
                or normalized_result.get("signal")
                or "UNKNOWN"
            )
        
        confidence_value = normalized_result.get("confidence")
        if confidence_value is None:
            conf_str = str(normalized_result.get("confidence_in_decision", "")).upper()
            conf_map = {"LOW": 0.35, "MEDIUM": 0.55, "HIGH": 0.8}
            confidence_value = conf_map.get(conf_str, 0.5)
        try:
            confidence_value = float(confidence_value)
        except (TypeError, ValueError):
            confidence_value = 0.5
        
        reasoning_text = (
            normalized_result.get("reason")
            or normalized_result.get("reasoning")
            or normalized_result.get("combined_reasoning")
            or raw_response[:500]
        )
        
        # Timestamps
        now = datetime.now()
        created_at_iso = now.astimezone().isoformat()
        
        # Generar embedding
        embedding_text = f"{prompt}\n{reasoning_text}".strip()
        embedding_json = None
        if self.use_embeddings:
            emb_vec = self._embed_text(embedding_text)
            if emb_vec:
                embedding_json = json.dumps(emb_vec)
        
        # Insertar en SQLite
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                try:
                    conn.execute("""
                        INSERT INTO reasoning_entries (
                            agent, prompt_digest, prompt, reasoning, action, confidence,
                            backend, latency_ms, metadata, created_at, embedding
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        agent_name, digest, prompt, str(reasoning_text), action_value,
                        confidence_value, backend, latency_ms,
                        json.dumps(metadata or {}), created_at_iso, embedding_json
                    ))
                except sqlite3.IntegrityError:
                    # Digest duplicado, actualizar
                    logger.debug(f"Duplicate digest {digest}, updating")
                    conn.execute("""
                        UPDATE reasoning_entries SET
                            reasoning = ?, action = ?, confidence = ?, backend = ?,
                            latency_ms = ?, metadata = ?, created_at = ?, embedding = ?
                        WHERE agent = ? AND prompt_digest = ?
                    """, (str(reasoning_text), action_value, confidence_value, backend,
                          latency_ms, json.dumps(metadata or {}), created_at_iso, embedding_json,
                          agent_name, digest))
                
                # Prune si supera límite por agente
                conn.execute("""
                    DELETE FROM reasoning_entries WHERE agent = ? AND prompt_digest NOT IN (
                        SELECT prompt_digest FROM reasoning_entries
                        WHERE agent = ? ORDER BY created_at DESC LIMIT ?
                    )
                """, (agent_name, agent_name, self.max_entries_per_agent))
        
        logger.info(f"ReasoningBank: Stored entry for {agent_name} with digest {digest[:8]}")
        
        return ReasoningEntryOptimized(
            agent=agent_name,
            prompt_digest=digest,
            prompt=prompt,
            reasoning=str(reasoning_text),
            action=action_value,
            confidence=confidence_value,
            backend=backend,
            latency_ms=latency_ms,
            metadata={**(metadata or {}), "analysis_timestamp": now.strftime("%Y-%m-%d %H:%M:%S %Z")},
            created_at=created_at_iso,
            embedding=embedding_json,
        )
    
    def get_recent(self, agent_name: str, limit: int = 5) -> List["ReasoningEntryOptimized"]:
        """Obtiene entries recientes (O(log n) con índice)."""
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT * FROM reasoning_entries
                    WHERE agent = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (agent_name, limit))
                
                rows = cursor.fetchall()
                return [self._row_to_entry(row) for row in rows]
    
    def _row_to_entry(self, row: sqlite3.Row) -> "ReasoningEntryOptimized":
        """Convierte un row SQLite a ReasoningEntry."""
        return ReasoningEntryOptimized(
            agent=row["agent"],
            prompt_digest=row["prompt_digest"],
            prompt=row["prompt"],
            reasoning=row["reasoning"] or "",
            action=row["action"] or "",
            confidence=row["confidence"] or 0.0,
            backend=row["backend"] or "",
            latency_ms=row["latency_ms"],
            metadata=json.loads(row["metadata"] or "{}"),
            created_at=row["created_at"] or "",
            embedding=row["embedding"],
            success=bool(row["success"]) if row["success"] is not None else None,
            reward=row["reward"],
            reward_signal=row["reward_signal"],
            near_miss=bool(row["near_miss"]) if row["near_miss"] is not None else None,
            reward_notes=row["reward_notes"],
            evaluated_at=row["evaluated_at"],
            trade_id=row["trade_id"],
            judge_verdict=row["judge_verdict"],
            judge_score=row["judge_score"],
            judge_confidence=row["judge_confidence"],
            judge_notes=row["judge_notes"],
            judge_tags=row["judge_tags"],
            judge_metadata=row["judge_metadata"],
            judge_success_estimate=bool(row["judge_success_estimate"]) if row["judge_success_estimate"] is not None else None,
            judged_at=row["judged_at"],
        )
    
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
        """Actualiza outcome de una entry (O(log n) con índice)."""
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("""
                    UPDATE reasoning_entries SET
                        success = ?, reward = ?, trade_id = ?, reward_signal = ?,
                        near_miss = ?, reward_notes = ?, evaluated_at = ?
                    WHERE agent = ? AND prompt_digest = ?
                """, (
                    1 if success else 0, reward, trade_id, reward_signal,
                    1 if near_miss else 0 if near_miss is not None else None,
                    reward_notes, datetime.utcnow().isoformat(),
                    agent_name, prompt_digest
                ))
                
                if cursor.rowcount > 0:
                    logger.info(f"ReasoningBank: Updated outcome for {prompt_digest[:8]}")
                    return True
                else:
                    logger.warning(f"ReasoningBank: Entry not found: {prompt_digest[:8]}")
                    return False
    
    def attach_judge_feedback(
        self,
        agent_name: str,
        prompt_digest: str,
        judge_payload: Dict[str, Any]
    ) -> bool:
        """Atacha feedback del LLM-as-Judge."""
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                success_estimate = judge_payload.get("success_estimate")
                cursor = conn.execute("""
                    UPDATE reasoning_entries SET
                        judge_verdict = ?, judge_score = ?, judge_confidence = ?,
                        judge_notes = ?, judge_tags = ?, judge_metadata = ?,
                        judge_success_estimate = ?, judged_at = ?
                    WHERE agent = ? AND prompt_digest = ?
                """, (
                    judge_payload.get("verdict"),
                    judge_payload.get("score"),
                    judge_payload.get("confidence"),
                    judge_payload.get("notes"),
                    json.dumps(judge_payload.get("tags") or []),
                    json.dumps(judge_payload.get("metadata") or {}),
                    1 if success_estimate else 0 if success_estimate is not None else None,
                    datetime.utcnow().isoformat(),
                    agent_name, prompt_digest
                ))
                
                if cursor.rowcount > 0:
                    logger.info(f"ReasoningBank: Judge feedback attached to {prompt_digest[:8]}")
                    return True
                return False
    
    def search(self, agent_name: str, query: str, limit: int = 5) -> List["ReasoningEntryOptimized"]:
        """Búsqueda por keywords (full-scan, optimizado con índice)."""
        query_lower = f"%{query.lower()}%"
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT * FROM reasoning_entries
                    WHERE agent = ? AND (
                        LOWER(prompt) LIKE ? OR
                        LOWER(reasoning) LIKE ?
                    )
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (agent_name, query_lower, query_lower, limit))
                
                rows = cursor.fetchall()
                return [self._row_to_entry(row) for row in rows]
    
    def get_relevant_context(
        self,
        agent_name: str,
        current_prompt: str,
        limit: int = 3,
        min_similarity: float = 0.3,
        prefer_successful: bool = True
    ) -> List["ReasoningEntryOptimized"]:
        """Recupera contexto relevante con similitud."""
        # Obtener embedding del prompt actual
        current_embedding = None
        if self.use_embeddings:
            current_embedding = self._embed_text(current_prompt)
        
        # Obtener entradas recientes
        entries = self.get_recent(agent_name, self.max_entries_per_agent)
        if not entries:
            return []
        
        # Calcular similitud
        scored = []
        for entry in entries:
            score = self._calculate_similarity(current_embedding, entry)
            if score >= min_similarity:
                # Boost para exitosas
                if prefer_successful and entry.success is True:
                    score *= 1.5
                scored.append((score, entry))
        
        # Ordenar y retornar top N
        scored.sort(key=lambda x: x[0], reverse=True)
        return [entry for _, entry in scored[:limit]]
    
    def _calculate_similarity(
        self,
        current_embedding: Optional[List[float]],
        entry: "ReasoningEntryOptimized"
    ) -> float:
        """Calcula similitud entre embeddings o usa fallback."""
        if current_embedding and entry.embedding:
            # Usar cosine similarity
            try:
                entry_vec = json.loads(entry.embedding)
                if len(entry_vec) == len(current_embedding):
                    dot = sum(a * b for a, b in zip(current_embedding, entry_vec))
                    norm_curr = sum(a * a for a in current_embedding) ** 0.5
                    norm_entry = sum(a * a for a in entry_vec) ** 0.5
                    if norm_curr > 0 and norm_entry > 0:
                        return dot / (norm_curr * norm_entry)
            except Exception:
                pass
        
        # Fallback: keyword overlap
        current_words = set(str(current_embedding) if current_embedding else []).split())
        entry_words = set(str(entry.prompt).lower().split())
        overlap = current_words | entry_words
        if overlap:
            intersection = current_words & entry_words
            return len(intersection) / len(overlap)
        return 0.0
    
    def get_success_rate(self, agent_name: str, lookback: int = 50) -> Dict[str, Any]:
        """Calcula tasa de éxito para un agente."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT success, reward FROM reasoning_entries
                WHERE agent = ? AND success IS NOT NULL
                ORDER BY created_at DESC
                LIMIT ?
            """, (agent_name, lookback))
            
            rows = cursor.fetchall()
            if not rows:
                return {"total_evaluated": 0, "success_rate": 0.0, "avg_reward": 0.0}
            
            successful = sum(1 for r in rows if r[0] == 1)
            total_reward = sum(r[1] or 0.0 for r in rows)
            
            return {
                "total_evaluated": len(rows),
                "successful": successful,
                "success_rate": successful / len(rows),
                "avg_reward": total_reward / len(rows),
                "total_reward": total_reward
            }


@dataclass
class ReasoningEntryOptimized:
    """Reasoning entry compatible con API del original."""
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
    embedding: Optional[str] = None  # JSON string
    
    # Outcome fields
    success: Optional[bool] = None
    reward: Optional[float] = None
    reward_signal: Optional[float] = None
    near_miss: Optional[bool] = None
    reward_notes: Optional[str] = None
    evaluated_at: Optional[str] = None
    trade_id: Optional[str] = None
    
    # Judge feedback fields
    judge_verdict: Optional[str] = None
    judge_score: Optional[float] = None
    judge_confidence: Optional[float] = None
    judge_notes: Optional[str] = None
    judge_tags: Optional[str] = None
    judge_metadata: Optional[str] = None
    judge_success_estimate: Optional[bool] = None
    judged_at: Optional[str] = None


# Singleton
_reasoning_bank_opt: Optional[ReasoningBankOptimized] = None
_reasoning_lock = threading.Lock()


def get_reasoning_bank_optimized() -> ReasoningBankOptimized:
    """Obtiene o crea la instancia optimizada global."""
    global _reasoning_bank_opt
    if _reasoning_bank_opt is None:
        with _reasoning_lock:
            if _reasoning_bank_opt is None:
                _reasoning_bank_opt = ReasoningBankOptimized()
    return _reasoning_bank_opt
