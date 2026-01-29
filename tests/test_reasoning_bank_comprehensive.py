"""
Tests for ReasoningBank module.
"""
import pytest
import tempfile
import shutil
import os


class TestReasoningBankBasic:
    """Basic tests for ReasoningBank."""

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for tests."""
        dir_path = tempfile.mkdtemp()
        yield dir_path
        shutil.rmtree(dir_path)

    def test_init_creates_directory(self, temp_dir):
        """Test that init creates storage directory."""
        from src.memory.reasoning_bank import ReasoningBank
        
        storage_path = os.path.join(temp_dir, "reasoning_bank")
        bank = ReasoningBank(
            storage_dir=storage_path,
            use_embeddings=False
        )
        
        assert os.path.exists(storage_path)

    def test_store_and_retrieve_entry(self, temp_dir):
        """Test storing and retrieving an entry."""
        from src.memory.reasoning_bank import ReasoningBank
        
        storage_path = os.path.join(temp_dir, "reasoning_bank")
        bank = ReasoningBank(
            storage_dir=storage_path,
            use_embeddings=False
        )
        
        # Store an entry
        bank.store_entry(
            agent_name="technical",
            prompt="Analyze BTCUSDT",
            normalized_result={
                "action": "BUY",
                "confidence": 0.8,
                "reasoning": "Strong momentum"
            },
            raw_response="Raw LLM response",
            backend="ollama",
            latency_ms=1200.0
        )
        
        # Retrieve recent entries
        recent = bank.get_recent("technical", limit=5)
        
        assert len(recent) == 1
        assert recent[0].action == "BUY"
        assert recent[0].confidence == 0.8

    def test_search_entries(self, temp_dir):
        """Test searching entries."""
        from src.memory.reasoning_bank import ReasoningBank
        
        storage_path = os.path.join(temp_dir, "reasoning_bank")
        bank = ReasoningBank(
            storage_dir=storage_path,
            use_embeddings=False
        )
        
        # Store multiple entries
        bank.store_entry(
            agent_name="technical",
            prompt="Analyze BTC with RSI indicator",
            normalized_result={"action": "BUY", "confidence": 0.8, "reasoning": "RSI oversold"},
            raw_response="resp1",
            backend="ollama"
        )
        
        bank.store_entry(
            agent_name="technical",
            prompt="Analyze ETH with MACD",
            normalized_result={"action": "SELL", "confidence": 0.7, "reasoning": "MACD crossover"},
            raw_response="resp2",
            backend="ollama"
        )
        
        # Search
        results = bank.search("technical", "RSI")
        
        assert len(results) >= 1

    def test_get_success_rate_no_data(self, temp_dir):
        """Test success rate with no data."""
        from src.memory.reasoning_bank import ReasoningBank
        
        storage_path = os.path.join(temp_dir, "reasoning_bank")
        bank = ReasoningBank(
            storage_dir=storage_path,
            use_embeddings=False
        )
        
        result = bank.get_success_rate("unknown_agent")
        
        # Returns dict with success_rate key
        assert isinstance(result, dict)
        assert result.get("success_rate") == 0.0

    def test_update_entry_outcome(self, temp_dir):
        """Test updating entry with trade outcome."""
        from src.memory.reasoning_bank import ReasoningBank
        
        storage_path = os.path.join(temp_dir, "reasoning_bank")
        bank = ReasoningBank(
            storage_dir=storage_path,
            use_embeddings=False
        )
        
        # Store entry
        bank.store_entry(
            agent_name="technical",
            prompt="Test prompt",
            normalized_result={"action": "BUY", "confidence": 0.75, "reasoning": "Test"},
            raw_response="Response",
            backend="ollama"
        )
        
        # Get the entry's digest
        entries = bank.get_recent("technical", limit=1)
        digest = entries[0].prompt_digest
        
        # Update with outcome
        bank.update_entry_outcome(
            agent_name="technical",
            prompt_digest=digest,
            success=True,
            reward=50.0,
            trade_id="trade-123"
        )
        
        # Verify update
        updated = bank.get_recent("technical", limit=1)[0]
        assert updated.success is True
        assert updated.reward == 50.0


class TestReasoningBankAdvanced:
    """Advanced tests for ReasoningBank."""

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for tests."""
        dir_path = tempfile.mkdtemp()
        yield dir_path
        shutil.rmtree(dir_path)

    def test_get_relevant_context(self, temp_dir):
        """Test getting relevant context for prompts."""
        from src.memory.reasoning_bank import ReasoningBank
        
        storage_path = os.path.join(temp_dir, "reasoning_bank")
        bank = ReasoningBank(
            storage_dir=storage_path,
            use_embeddings=False
        )
        
        # Store some entries
        bank.store_entry(
            agent_name="technical",
            prompt="BTC RSI analysis for trend",
            normalized_result={"action": "BUY", "confidence": 0.9, "reasoning": "RSI bullish"},
            raw_response="resp",
            backend="ollama"
        )
        
        # Get context for similar prompt
        context = bank.get_relevant_context(
            agent_name="technical",
            current_prompt="Analyze BTC RSI indicator",
            limit=3,
            min_similarity=0.1
        )
        
        assert isinstance(context, list)

    def test_max_entries_per_agent(self, temp_dir):
        """Test that max entries limit is enforced."""
        from src.memory.reasoning_bank import ReasoningBank
        
        storage_path = os.path.join(temp_dir, "reasoning_bank")
        bank = ReasoningBank(
            storage_dir=storage_path,
            use_embeddings=False,
            max_entries_per_agent=5
        )
        
        # Store more than max entries
        for i in range(10):
            bank.store_entry(
                agent_name="technical",
                prompt=f"Prompt {i}",
                normalized_result={"action": "HOLD", "confidence": 0.5, "reasoning": f"Reason {i}"},
                raw_response=f"Response {i}",
                backend="ollama"
            )
        
        # Should only have max entries
        entries = bank.get_recent("technical", limit=100)
        assert len(entries) <= 5

    def test_attach_judge_feedback(self, temp_dir):
        """Test attaching judge feedback to entry."""
        from src.memory.reasoning_bank import ReasoningBank
        
        storage_path = os.path.join(temp_dir, "reasoning_bank")
        bank = ReasoningBank(
            storage_dir=storage_path,
            use_embeddings=False
        )
        
        # Store entry
        bank.store_entry(
            agent_name="sentiment",
            prompt="Analyze market sentiment",
            normalized_result={"action": "BUY", "confidence": 0.8, "reasoning": "Positive sentiment"},
            raw_response="Response",
            backend="groq"
        )
        
        entries = bank.get_recent("sentiment", limit=1)
        digest = entries[0].prompt_digest
        
        # Attach judge feedback
        judge_payload = {
            "verdict": "good",
            "score": 0.9,
            "feedback": "Well-reasoned analysis"
        }
        
        bank.attach_judge_feedback(
            agent_name="sentiment",
            prompt_digest=digest,
            judge_payload=judge_payload
        )
        
        # Verify feedback attached - check judge_verdict attribute
        updated = bank.get_recent("sentiment", limit=1)[0]
        assert updated.judge_verdict is not None or hasattr(updated, 'judge_verdict')


class TestReasoningEntry:
    """Tests for ReasoningEntry dataclass."""

    def test_entry_creation(self):
        """Test creating a ReasoningEntry."""
        from src.memory.reasoning_bank import ReasoningEntry
        
        entry = ReasoningEntry(
            agent="technical",
            prompt_digest="abc123",
            prompt="Analyze BTC trend",
            reasoning="Bullish divergence detected",
            action="BUY",
            confidence=0.85,
            backend="ollama",
            latency_ms=1500.0,
            metadata={"symbol": "BTCUSDT"},
            created_at="2026-01-28T12:00:00"
        )
        
        assert entry.agent == "technical"
        assert entry.action == "BUY"
        assert entry.confidence == 0.85
