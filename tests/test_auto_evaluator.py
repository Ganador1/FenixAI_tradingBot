from datetime import datetime, timedelta, timezone

import pytest

from src.analysis.auto_evaluator import AutoEvaluator
from src.memory.reasoning_bank import ReasoningBank


@pytest.mark.asyncio
async def test_auto_evaluator_buy_success(tmp_path, monkeypatch):
    # Create isolated ReasoningBank for testing
    rb = ReasoningBank(storage_dir=str(tmp_path), use_embeddings=False)
    # Monkeypatch getter to return isolated bank
    import src.analysis.auto_evaluator as ae_module
    import src.memory.reasoning_bank as rb_module
    rb_module.get_reasoning_bank = lambda: rb  # type: ignore
    ae_module.get_reasoning_bank = lambda: rb  # type: ignore

    bank = rb

    entry = bank.store_entry(
        agent_name='decision_agent',
        prompt='Test buy',
        normalized_result={'action': 'BUY', 'confidence': 0.9},
        raw_response='buy',
        backend='test',
    )

    # Adjust created_at to be in the past
    created_dt = datetime.now(timezone.utc) - timedelta(minutes=10)
    entry.created_at = created_dt.isoformat()

    # Prepare fake klines: open lower than close to simulate price increase
    async def fake_get_klines(**kwargs):
        start_time = kwargs.get('start_time')
        return [
            {'open': '100.0', 'close': '101.0', 'timestamp': start_time or 0},
        ]

    evaluator = AutoEvaluator(symbol='BTCUSDT', evaluation_horizon_minutes=0)
    # Patch its client method
    monkeypatch.setattr(evaluator.client, 'get_klines', fake_get_klines)

    await evaluator.evaluate_pending_entries()

    recent = bank.get_recent('decision_agent', limit=10)
    matched = [e for e in recent if e.prompt_digest == entry.prompt_digest]

    assert len(matched) == 1
    assert matched[0].success is True


@pytest.mark.asyncio
async def test_auto_evaluator_skips_entries_linked_to_open_real_trades(tmp_path, monkeypatch):
    rb = ReasoningBank(storage_dir=str(tmp_path), use_embeddings=False)
    import src.analysis.auto_evaluator as ae_module
    import src.memory.reasoning_bank as rb_module

    rb_module.get_reasoning_bank = lambda: rb  # type: ignore
    ae_module.get_reasoning_bank = lambda: rb  # type: ignore
    entry = rb.store_entry(
        agent_name="decision_agent",
        prompt="Live BUY",
        normalized_result={"action": "BUY", "confidence": 0.8},
        raw_response="BUY",
        backend="test",
        latency_ms=10.0,
    )
    assert rb.attach_trade_reference("decision_agent", entry.prompt_digest, "order-live-1")

    evaluator = AutoEvaluator(symbol="BTCUSDT", evaluation_horizon_minutes=0)

    async def unexpected_price_lookup(**_kwargs):
        raise AssertionError("open live trade must not be paper-evaluated")

    monkeypatch.setattr(evaluator.client, "get_klines", unexpected_price_lookup)
    await evaluator.evaluate_pending_entries()

    pending = rb.get_recent("decision_agent", limit=1)[0]
    assert pending.trade_id == "order-live-1"
    assert pending.success is None


@pytest.mark.asyncio
async def test_auto_evaluator_sell_success(tmp_path, monkeypatch):
    rb = ReasoningBank(storage_dir=str(tmp_path), use_embeddings=False)
    import src.analysis.auto_evaluator as ae_module
    import src.memory.reasoning_bank as rb_module
    rb_module.get_reasoning_bank = lambda: rb  # type: ignore
    ae_module.get_reasoning_bank = lambda: rb  # type: ignore

    bank = rb

    entry = bank.store_entry(
        agent_name='decision_agent',
        prompt='Test sell',
        normalized_result={'action': 'SELL', 'confidence': 0.9},
        raw_response='sell',
        backend='test',
    )

    created_dt = datetime.now(timezone.utc) - timedelta(minutes=10)
    entry.created_at = created_dt.isoformat()

    async def fake_get_klines(**kwargs):
        start_time = kwargs.get('start_time')
        return [
            {'open': '101.0', 'close': '100.0', 'timestamp': start_time or 0},
        ]

    evaluator = AutoEvaluator(symbol='BTCUSDT', evaluation_horizon_minutes=0)
    monkeypatch.setattr(evaluator.client, 'get_klines', fake_get_klines)

    await evaluator.evaluate_pending_entries()

    recent = bank.get_recent('decision_agent', limit=10)
    matched = [e for e in recent if e.prompt_digest == entry.prompt_digest]

    assert len(matched) == 1
    assert matched[0].success is True


@pytest.mark.asyncio
async def test_auto_evaluator_evaluates_duplicate_digest_once(tmp_path, monkeypatch):
    rb = ReasoningBank(storage_dir=str(tmp_path), use_embeddings=False)
    import src.analysis.auto_evaluator as ae_module
    import src.memory.reasoning_bank as rb_module
    rb_module.get_reasoning_bank = lambda: rb  # type: ignore
    ae_module.get_reasoning_bank = lambda: rb  # type: ignore

    first = rb.store_entry(
        agent_name='decision_agent',
        prompt='Repeated decision prompt',
        normalized_result={'action': 'BUY', 'confidence': 0.9},
        raw_response='buy',
        backend='test',
    )
    second = rb.store_entry(
        agent_name='decision_agent',
        prompt='Repeated decision prompt',
        normalized_result={'action': 'BUY', 'confidence': 0.9},
        raw_response='buy',
        backend='test',
    )
    assert first.prompt_digest == second.prompt_digest

    created_dt = datetime.now(timezone.utc) - timedelta(minutes=10)
    for entry in rb.get_recent('decision_agent', limit=10):
        entry.created_at = created_dt.isoformat()

    kline_calls = 0

    async def fake_get_klines(**kwargs):
        nonlocal kline_calls
        kline_calls += 1
        return [
            {'open': '100.0', 'close': '101.0', 'timestamp': kwargs.get('start_time') or 0},
        ]

    evaluator = AutoEvaluator(symbol='BTCUSDT', evaluation_horizon_minutes=0)
    monkeypatch.setattr(evaluator.client, 'get_klines', fake_get_klines)

    await evaluator.evaluate_pending_entries()

    matched = [
        entry
        for entry in rb.get_recent('decision_agent', limit=10)
        if entry.prompt_digest == first.prompt_digest
    ]
    assert kline_calls == 1
    assert len(matched) == 2
    assert all(entry.success is True for entry in matched)


@pytest.mark.asyncio
async def test_unknown_action_is_marked_not_evaluable_without_market_request(tmp_path, monkeypatch):
    rb = ReasoningBank(storage_dir=str(tmp_path), use_embeddings=False)
    import src.analysis.auto_evaluator as ae_module

    monkeypatch.setattr(ae_module, "get_reasoning_bank", lambda: rb)
    entry = rb.store_entry(
        agent_name="qabba_agent",
        prompt="Raw microstructure snapshot",
        normalized_result={"action": "UNKNOWN", "confidence": 0.4},
        raw_response="{}",
        backend="test",
    )
    entry.created_at = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    evaluator = AutoEvaluator(symbol="BTCUSDT", evaluation_horizon_minutes=0)

    async def unexpected_market_request(**_kwargs):
        raise AssertionError("non-evaluable entries must not request Binance klines")

    monkeypatch.setattr(evaluator.client, "get_klines", unexpected_market_request)
    await evaluator.evaluate_pending_entries()
    await evaluator.evaluate_pending_entries()

    stored = rb.get_recent("qabba_agent", limit=1)[0]
    assert stored.success is None
    assert stored.metadata["auto_evaluator_status"] == "not_evaluable"
    assert stored.metadata["auto_evaluator_reason"] == "unknown_or_non_directional_action"
