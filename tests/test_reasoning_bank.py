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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
