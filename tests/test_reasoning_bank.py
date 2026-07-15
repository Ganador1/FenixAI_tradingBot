"""
Tests para ReasoningBank - Sistema de memoria semántica.
"""
import pytest
from datetime import datetime


class TestReasoningEntry:
    """Tests para ReasoningEntry dataclass."""

    def test_entry_creation(self):
        """Verificar creación de una entrada."""
        from src.memory.reasoning_bank import ReasoningEntry

        entry = ReasoningEntry(
            agent="technical",
            prompt_digest="abc123",
            prompt="Analyze BTCUSDT",
            reasoning="RSI shows oversold conditions",
            action="BUY",
            confidence=0.75,
            backend="ollama",
            latency_ms=150.5,
            metadata={"symbol": "BTCUSDT"},
            created_at=datetime.now().isoformat(),
        )

        assert entry.agent == "technical"
        assert entry.action == "BUY"
        assert entry.confidence == 0.75

    def test_entry_matches_query(self):
        """Verificar búsqueda por coincidencia de texto."""
        from src.memory.reasoning_bank import ReasoningEntry

        entry = ReasoningEntry(
            agent="technical",
            prompt_digest="abc123",
            prompt="Analyze BTCUSDT market",
            reasoning="RSI shows oversold conditions at support",
            action="BUY",
            confidence=0.75,
            backend="ollama",
            latency_ms=150.5,
            metadata={},
            created_at=datetime.now().isoformat(),
        )

        assert entry.matches("oversold")
        assert entry.matches("RSI")
        assert entry.matches("BTCUSDT")
        assert not entry.matches("ETHUSDT")

    def test_keyword_overlap_similarity(self):
        """Verificar cálculo de similitud por overlap."""
        from src.memory.reasoning_bank import ReasoningEntry

        entry = ReasoningEntry(
            agent="technical",
            prompt_digest="abc123",
            prompt="Analyze BTCUSDT RSI MACD",
            reasoning="Technical analysis",
            action="BUY",
            confidence=0.75,
            backend="ollama",
            latency_ms=150.5,
            metadata={},
            created_at=datetime.now().isoformat(),
        )

        # Mismo prompt debería tener alta similitud
        similarity = entry._keyword_overlap("Analyze BTCUSDT RSI MACD")
        assert similarity == 1.0

        # Prompt diferente debería tener menor similitud
        similarity = entry._keyword_overlap("Different prompt here")
        assert similarity < 0.5


class TestReasoningBank:
    """Tests para ReasoningBank."""

    @pytest.fixture
    def reasoning_bank(self, tmp_path):
        """Crear instancia de ReasoningBank."""
        from src.memory.reasoning_bank import ReasoningBank
        storage_dir = str(tmp_path / "reasoning_bank")
        return ReasoningBank(
            storage_dir=storage_dir,
            max_entries_per_agent=100,
            use_embeddings=False
        )

    def test_store_entry(self, reasoning_bank):
        """Verificar almacenamiento de entrada."""
        entry = reasoning_bank.store_entry(
            agent_name="technical",
            prompt="Analyze BTCUSDT",
            normalized_result={"action": "BUY", "confidence": 0.75},
            raw_response="BUY signal detected",
            backend="ollama",
            latency_ms=150.0,
        )

        assert entry is not None

    def test_get_recent_entries(self, reasoning_bank):
        """Verificar obtención de entradas recientes."""
        # Almacenar varias entradas
        for i in range(5):
            reasoning_bank.store_entry(
                agent_name="technical",
                prompt=f"Prompt {i}",
                normalized_result={"action": "BUY", "confidence": 0.7},
                raw_response="Response",
                backend="ollama",
                latency_ms=100.0,
            )

        recent = reasoning_bank.get_recent(agent_name="technical", limit=3)
        assert len(recent) == 3

    def test_get_entries_by_agent(self, reasoning_bank):
        """Verificar filtrado por agente."""
        reasoning_bank.store_entry(
            agent_name="technical",
            prompt="Tech prompt",
            normalized_result={"action": "BUY"},
            raw_response="",
            backend="ollama",
            latency_ms=100.0,
        )
        reasoning_bank.store_entry(
            agent_name="sentiment",
            prompt="Sentiment prompt",
            normalized_result={"action": "HOLD"},
            raw_response="",
            backend="ollama",
            latency_ms=100.0,
        )

        tech_entries = reasoning_bank.get_recent(agent_name="technical", limit=10)
        assert len(tech_entries) >= 1

    def test_search_similar(self, reasoning_bank):
        """Verificar búsqueda de entradas similares."""
        reasoning_bank.store_entry(
            agent_name="technical",
            prompt="BTCUSDT RSI oversold analysis",
            normalized_result={"action": "BUY"},
            raw_response="",
            backend="ollama",
            latency_ms=100.0,
        )

        # search usa embeddings, si están desactivados retorna lista vacía
        similar = reasoning_bank.search(
            agent_name="technical",
            query="RSI oversold",
            limit=5
        )
        assert isinstance(similar, list)

    def test_update_entry_outcome_updates_duplicate_digests(self, reasoning_bank):
        """Verificar que duplicados del mismo prompt no quedan pendientes."""
        first = reasoning_bank.store_entry(
            agent_name="technical",
            prompt="Repeated market prompt",
            normalized_result={"action": "BUY", "confidence": 0.7},
            raw_response="Response",
            backend="ollama",
            latency_ms=100.0,
        )
        second = reasoning_bank.store_entry(
            agent_name="technical",
            prompt="Repeated market prompt",
            normalized_result={"action": "BUY", "confidence": 0.7},
            raw_response="Response",
            backend="ollama",
            latency_ms=100.0,
        )

        assert first.prompt_digest == second.prompt_digest

        updated = reasoning_bank.update_entry_outcome(
            agent_name="technical",
            prompt_digest=first.prompt_digest,
            success=True,
            reward=0.42,
            reward_notes="dedupe check",
        )

        assert updated is True
        matches = [
            entry
            for entry in reasoning_bank.get_recent(agent_name="technical", limit=10)
            if entry.prompt_digest == first.prompt_digest
        ]
        assert len(matches) == 2
        assert all(entry.success is True for entry in matches)
        assert all(entry.reward == 0.42 for entry in matches)

    def test_attach_trade_reference_keeps_entry_pending_for_realized_close(self, reasoning_bank):
        entry = reasoning_bank.store_entry(
            agent_name="decision_agent",
            prompt="Trade decision",
            normalized_result={"action": "BUY", "confidence": 0.8},
            raw_response="BUY",
            backend="test",
            latency_ms=10.0,
        )

        attached = reasoning_bank.attach_trade_reference(
            "decision_agent", entry.prompt_digest, "binance-order-123"
        )

        assert attached is True
        updated = reasoning_bank.get_recent("decision_agent", limit=1)[0]
        assert updated.trade_id == "binance-order-123"
        assert updated.success is None

    def test_mark_entry_not_evaluable_persists_terminal_skip(self, reasoning_bank):
        entry = reasoning_bank.store_entry(
            agent_name="qabba_agent",
            prompt="No directional output",
            normalized_result={"action": "UNKNOWN"},
            raw_response="{}",
            backend="test",
        )

        assert reasoning_bank.mark_entry_not_evaluable(
            "qabba_agent",
            entry.prompt_digest,
            reason="unknown_action",
        )

        stored = reasoning_bank.get_recent("qabba_agent", limit=1)[0]
        assert stored.success is None
        assert stored.metadata["auto_evaluator_status"] == "not_evaluable"
        assert stored.metadata["auto_evaluator_reason"] == "unknown_action"


class TestReasoningBankPersistence:
    """Tests para persistencia de ReasoningBank."""

    def test_save_and_load(self, tmp_path):
        """Verificar guardado y carga - ReasoningBank persiste automáticamente."""
        from src.memory.reasoning_bank import ReasoningBank

        storage_dir = str(tmp_path / "reasoning_bank")
        
        # Crear y poblar
        bank = ReasoningBank(
            storage_dir=storage_dir,
            max_entries_per_agent=100,
            use_embeddings=False
        )
        bank.store_entry(
            agent_name="technical",
            prompt="Test prompt",
            normalized_result={"action": "BUY"},
            raw_response="",
            backend="ollama",
            latency_ms=100.0,
        )

        # Crear nueva instancia con mismo storage_dir
        bank2 = ReasoningBank(
            storage_dir=storage_dir,
            max_entries_per_agent=100,
            use_embeddings=False
        )

        # Verificar que las entradas se recuperan
        recent = bank2.get_recent(agent_name="technical", limit=10)
        assert len(recent) >= 1


class TestQuarantine:
    """Quarantined ghost entries must never re-enter agent prompts."""

    @pytest.fixture
    def bank(self, tmp_path):
        from src.memory.reasoning_bank import ReasoningBank

        return ReasoningBank(
            storage_dir=str(tmp_path / "reasoning_bank"),
            max_entries_per_agent=100,
            use_embeddings=False,
        )

    def _store(self, bank, prompt, quarantined=False):
        entry = bank.store_entry(
            agent_name="decision",
            prompt=prompt,
            normalized_result={"action": "BUY", "confidence": 0.7},
            raw_response="BUY momentum breakout confirmed",
            backend="ollama",
            latency_ms=100.0,
        )
        if quarantined:
            entry.metadata["quarantined"] = "fanin-duplicate-2026-07"
        return entry

    def test_quarantined_excluded_from_relevant_context(self, bank):
        self._store(bank, "momentum breakout long ETHUSDC", quarantined=True)
        clean = self._store(bank, "momentum breakout long ETHUSDC again")

        results = bank.get_relevant_context(
            agent_name="decision",
            current_prompt="momentum breakout long ETHUSDC",
            limit=5,
            min_similarity=0.1,
        )

        digests = [entry.prompt_digest for entry in results]
        assert clean.prompt_digest in digests
        assert len(results) == 1

    def test_quarantined_excluded_from_search(self, bank):
        self._store(bank, "capitulation wick reversal", quarantined=True)

        assert bank.search("decision", "capitulation") == []

    def test_null_metadata_is_tolerated(self, bank):
        entry = self._store(bank, "null metadata entry")
        entry.metadata = None

        results = bank.search("decision", "null metadata")
        assert len(results) == 1


class TestQuarantineScript:
    """Duplicate detection for the 2026-07 fan-in ghost entries."""

    def _record(self, digest, created_at, agent="decision", quarantined=False):
        import json

        metadata = {"quarantined": "x"} if quarantined else {}
        return json.dumps(
            {
                "agent": agent,
                "prompt_digest": digest,
                "prompt": "p",
                "reasoning": "r",
                "action": "BUY",
                "confidence": 0.7,
                "backend": "ollama",
                "latency_ms": 1.0,
                "metadata": metadata,
                "created_at": created_at,
            }
        )

    def test_dry_run_counts_and_apply_marks(self, tmp_path):
        import json

        from scripts.quarantine_reasoning_bank_duplicates import process_file

        jsonl = tmp_path / "decision_agent.jsonl"
        jsonl.write_text(
            "\n".join(
                [
                    self._record("dup-1", "2026-07-09T10:00:00+00:00"),
                    self._record("dup-1", "2026-07-09T10:00:02+00:00"),
                    self._record("unique-1", "2026-07-09T10:15:00+00:00"),
                    # Same digest but hours apart: a legitimate re-analysis.
                    self._record("dup-1", "2026-07-09T22:00:00+00:00"),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        dry = process_file(jsonl, window_sec=300.0, apply=False)
        assert dry["duplicates"] == 1
        assert dry["applied"] is False
        # Dry run must not modify the file.
        assert "quarantined" not in jsonl.read_text(encoding="utf-8")

        applied = process_file(jsonl, window_sec=300.0, apply=True)
        assert applied["duplicates"] == 1
        assert applied["applied"] is True

        records = [
            json.loads(line)
            for line in jsonl.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(records) == 4
        flags = [bool((r.get("metadata") or {}).get("quarantined")) for r in records]
        assert flags == [False, True, False, False]

        # Idempotent: a second pass finds nothing new.
        again = process_file(jsonl, window_sec=300.0, apply=True)
        assert again["duplicates"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
