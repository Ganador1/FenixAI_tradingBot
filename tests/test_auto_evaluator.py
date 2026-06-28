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
