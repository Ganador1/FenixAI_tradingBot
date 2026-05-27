#!/usr/bin/env python3
"""
Test de integración con Binance real.

Conecta al WebSocket @depth20, acumula datos y ejecuta exactamente
el mismo flujo de training que NanoFenix live.
Limita a N ticks para un test controlado.
"""
import asyncio
import collections
import json
import logging
import os
import sys
import time
import faulthandler
import traceback

import numpy as np
import websockets

faulthandler.enable()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nanofenix.feature_engine import NanoFeatureEngine, LOBSnapshot
from nanofenix.return_predictor import ReturnPredictor
from nanofenix.neural_predictor import NeuralPredictor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)

logger = logging.getLogger("TestBinance")

# ---- Configuración del test ----
SYMBOL = "btcusdt"
MAX_TICKS = 3000       # Suficiente para disparar training (2000 + 500 + buffer)
RETRAIN_EVERY = 1000
MIN_TRAINING = 2000
HORIZON = 500
WS_URL = f"wss://stream.binance.com:9443/ws/{SYMBOL}@depth20@100ms"


async def run_integration_test():
    """Ejecuta test de integración con datos reales de Binance."""
    
    logger.info("=" * 60)
    logger.info("NanoFenix v2.0 — Integration Test con Binance Real")
    logger.info("=" * 60)
    logger.info(f"  Symbol: {SYMBOL.upper()}")
    logger.info(f"  Max ticks: {MAX_TICKS}")
    logger.info(f"  Retrain every: {RETRAIN_EVERY}")
    logger.info(f"  Min training samples: {MIN_TRAINING}")
    logger.info(f"  WS: {WS_URL}")
    logger.info("=" * 60)
    
    predictor = ReturnPredictor(
        prediction_horizon=HORIZON,
        min_profitable_return_bps=3.0,
        retrain_every_n=RETRAIN_EVERY,
        min_training_samples=MIN_TRAINING,
        symbol=f"{SYMBOL}_integration_test",
    )
    
    logger.info(f"Device: {predictor.neural_model.device}")
    logger.info(f"Model params: {predictor.neural_model.model.count_parameters():,}")
    
    tick_count = 0
    training_triggered = False
    training_completed = False
    first_prediction = False
    
    try:
        async with websockets.connect(WS_URL, ping_interval=20) as ws:
            logger.info("✅ Conectado al WebSocket de Binance")
            
            async for raw in ws:
                data = json.loads(raw)
                bids_raw = data.get("bids", [])
                asks_raw = data.get("asks", [])
                
                if not bids_raw or not asks_raw:
                    continue
                
                bids = [(float(p), float(q)) for p, q in bids_raw[:20]]
                asks = [(float(p), float(q)) for p, q in asks_raw[:20]]
                
                snap = LOBSnapshot(
                    bid=bids[0][0],
                    bid_qty=bids[0][1],
                    ask=asks[0][0],
                    ask_qty=asks[0][1],
                    bids=bids,
                    asks=asks,
                    timestamp_ms=int(time.time() * 1000),
                )
                
                tick_count += 1
                
                # Procesar tick (esto es donde se dispara el training)
                prediction = predictor.process_tick(snap)
                
                # Await training si toca (bloquea handler para evitar concurrent numpy+PyTorch)
                if predictor._needs_retrain:
                    if not training_triggered:
                        training_triggered = True
                        logger.info("🔄 Training DISPARADO!")
                    await predictor._online_retrain_async()
                    if predictor.is_trained and not training_completed:
                        training_completed = True
                        logger.info("✅ Training COMPLETADO!")
                
                # Log periódico
                if tick_count % 500 == 0:
                    mid = (snap.bid + snap.ask) / 2
                    logger.info(
                        f"Tick #{tick_count:,} | BTC=${mid:,.2f} | "
                        f"Signal={prediction['signal']}({prediction['expected_return_bps']:+.1f}bps) | "
                        f"Buffer={len(predictor.tensor_seq_buffer)} | "
                        f"Trained={predictor.is_trained}"
                    )
                
                # Verificar primera predicción real
                if predictor.is_trained and prediction['signal'] != 'HOLD' and not first_prediction:
                    first_prediction = True
                    logger.info(f"🎯 Primera predicción real: {prediction['signal']} ({prediction['expected_return_bps']:+.1f}bps)")
                
                if tick_count >= MAX_TICKS:
                    break
                
                # Esperar a que el training termine si estamos cerca del final
                if tick_count >= MAX_TICKS - 100 and predictor._is_training:
                    logger.info("⏳ Esperando a que termine el training...")
                    while predictor._is_training:
                        await asyncio.sleep(0.1)
    
    except Exception as e:
        logger.error(f"❌ Error: {type(e).__name__}: {e}")
        traceback.print_exc()
        return False
    
    # Resumen
    logger.info("")
    logger.info("=" * 60)
    logger.info("RESULTADOS")
    logger.info("=" * 60)
    
    stats = predictor.get_stats()
    logger.info(f"  Ticks procesados: {stats['ticks_processed']:,}")
    logger.info(f"  Predictions made: {stats['predictions_made']:,}")
    logger.info(f"  Direction accuracy: {stats['direction_accuracy']:.1%}")
    logger.info(f"  Buffer size: {stats['buffer_size']}")
    logger.info(f"  Is trained: {stats['is_trained']}")
    logger.info(f"  Training triggered: {training_triggered}")
    logger.info(f"  Training completed: {training_completed}")
    logger.info(f"  First real prediction: {first_prediction}")
    
    # Verificar estado del modelo
    if predictor.is_trained:
        nan_params = sum(
            1 for name, p in predictor.neural_model.model.named_parameters()
            if p.isnan().any()
        )
        logger.info(f"  NaN params: {nan_params}")
        
        # Hacer predicciones post-training
        tseq = predictor.feature_engine.get_tensor_sequence(50)
        oseq = predictor.feature_engine.get_ofi_tensor_sequence(50)
        if tseq is not None:
            pred = predictor.neural_model.predict(tseq, oseq)
            logger.info(f"  Post-test prediction: {pred:.6f}")
            logger.info(f"  Prediction NaN: {np.isnan(pred)}")
    
    success = training_completed or (tick_count >= MAX_TICKS and not predictor._is_training)
    
    logger.info("=" * 60)
    status = "✅ PASS" if success else "❌ FAIL"
    logger.info(f"Test de integración: {status}")
    logger.info("=" * 60)
    
    # Cleanup
    test_model = f"nanofenix/models/tkan_{SYMBOL}_integration_test_v2.pth"
    test_lgb = f"nanofenix/models/nano_{SYMBOL}_integration_test_model.joblib"
    for f in [test_model, test_lgb]:
        if os.path.exists(f):
            os.remove(f)
    
    return success


if __name__ == "__main__":
    success = asyncio.run(run_integration_test())
    sys.exit(0 if success else 1)
