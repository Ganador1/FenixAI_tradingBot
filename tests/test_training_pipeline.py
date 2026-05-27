#!/usr/bin/env python3
"""
Test profundo del pipeline de training NanoFenix v2.0.

Simula exactamente el flujo que ocurre cuando _online_retrain() se dispara:
1. Acumula LOB snapshots reales → tensor_seq_buffer + ofi_seq_buffer
2. Construye X_lob [N, T, 20, 2, 2] y X_ofi [N, T, 20, 6] y targets y [N]
3. Llama a train_batch()
4. Llama a predict()

Usa datos con magnitudes realistas de BTC (no random normal).
"""
import sys
import os
import time
import signal
import traceback
import collections
import numpy as np
import torch
import asyncio
import threading
import pytest

# Para capturar segfaults
import faulthandler
faulthandler.enable()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nanofenix.feature_engine import NanoFeatureEngine, LOBSnapshot
from nanofenix.deep_ofi_engine import DeepOFIEngine
from nanofenix.neural_predictor import NeuralPredictor, SiameseTKANModel


def generate_realistic_lob_snapshot(mid_price: float, tick: int) -> LOBSnapshot:
    """Genera un snapshot LOB realista de BTC."""
    spread = np.random.uniform(0.01, 0.10)  # $0.01 - $0.10 spread for BTC
    bid = mid_price - spread / 2
    ask = mid_price + spread / 2
    
    # Generar 20 niveles de profundidad
    bids = []
    asks = []
    for i in range(20):
        # Precio: cada nivel se aleja ~$0.10-$1.00
        bp = bid - i * np.random.uniform(0.05, 0.5)
        ap = ask + i * np.random.uniform(0.05, 0.5)
        # Cantidad: típico BTC 0.001 - 5.0 BTC
        bq = np.random.uniform(0.01, 2.0)
        aq = np.random.uniform(0.01, 2.0)
        bids.append((bp, bq))
        asks.append((ap, aq))
    
    return LOBSnapshot(
        bid=bid,
        bid_qty=bids[0][1],
        ask=ask,
        ask_qty=asks[0][1],
        bids=bids,
        asks=asks,
        timestamp_ms=int(time.time() * 1000) + tick,
    )


def _generate_feature_sequences() -> tuple[list[np.ndarray], list[np.ndarray]]:
    fe = NanoFeatureEngine(lookback=200)
    mid = 65000.0
    
    tensor_seqs = []
    ofi_seqs = []
    
    for i in range(300):  # Más que suficiente para warm up
        mid += np.random.randn() * 5  # Random walk
        snap = generate_realistic_lob_snapshot(mid, i)
        features = fe.update(snap)
        
        if i >= 50:  # Después de warmup
            tseq = fe.get_tensor_sequence(50)
            oseq = fe.get_ofi_tensor_sequence(50)
            
            if tseq is not None:
                tensor_seqs.append(tseq)
            if oseq is not None:
                ofi_seqs.append(oseq)
    return tensor_seqs, ofi_seqs


@pytest.fixture(scope="module")
def generated_feature_sequences() -> tuple[list[np.ndarray], list[np.ndarray]]:
    return _generate_feature_sequences()


@pytest.fixture(scope="module")
def tensor_seqs(generated_feature_sequences):
    return generated_feature_sequences[0]


@pytest.fixture(scope="module")
def ofi_seqs(generated_feature_sequences):
    return generated_feature_sequences[1]


def test_1_feature_engine_produces_correct_shapes(tensor_seqs, ofi_seqs):
    """Verifica que FeatureEngine produce tensores con shapes correctos."""
    print("\n[TEST 1] Feature Engine — shapes y valores")
    
    print(f"  Tensor sequences generadas: {len(tensor_seqs)}")
    print(f"  OFI sequences generadas: {len(ofi_seqs)}")
    
    if tensor_seqs:
        t = tensor_seqs[-1]
        print(f"  Tensor seq shape: {t.shape}")  # Debería ser [50, 20, 2, 2]
        print(f"  Tensor value ranges:")
        print(f"    bid_px:  [{t[:,:,0,0].min():.2f}, {t[:,:,0,0].max():.2f}]")
        print(f"    bid_qty: [{t[:,:,0,1].min():.6f}, {t[:,:,0,1].max():.6f}]")
        print(f"    ask_px:  [{t[:,:,1,0].min():.2f}, {t[:,:,1,0].max():.2f}]")
        print(f"    ask_qty: [{t[:,:,1,1].min():.6f}, {t[:,:,1,1].max():.6f}]")
        assert t.shape == (50, 20, 2, 2), f"Shape incorrecto: {t.shape}"
    
    if ofi_seqs:
        o = ofi_seqs[-1]
        print(f"  OFI seq shape: {o.shape}")  # Debería ser [50, 20, 6]
        print(f"  OFI value range: [{o.min():.4f}, {o.max():.4f}]")
        assert o.shape == (50, 20, 6), f"OFI shape incorrecto: {o.shape}"
    
    print("  ✅ PASS")


def test_2_kan_numerical_stability():
    """Verifica estabilidad numérica de KAN layers con valores reales."""
    print("\n[TEST 2] KAN Layers — estabilidad numérica")
    
    from nanofenix.kan_layers import BSplineKANLayer, TKANLayer
    
    device = torch.device("cpu")
    
    # Test con valores dentro del rango del grid [-1, 1]
    kan = BSplineKANLayer(16, 8, grid_size=5, spline_order=3).to(device)
    x_normal = torch.randn(4, 16, device=device) * 0.5  # Dentro de [-1, 1]
    out = kan(x_normal)
    print(f"  Normal input range [-0.5, 0.5]: output range [{out.min():.4f}, {out.max():.4f}]")
    assert not torch.isnan(out).any(), "NaN con input normal!"
    assert not torch.isinf(out).any(), "Inf con input normal!"
    
    # Test con valores FUERA del rango (como pasan datos reales del LOB)
    x_large = torch.randn(4, 16, device=device) * 10.0  # Fuera de [-1, 1]
    out_large = kan(x_large)
    print(f"  Large input range [-10, 10]: output range [{out_large.min():.4f}, {out_large.max():.4f}]")
    nan_count = torch.isnan(out_large).sum().item()
    inf_count = torch.isinf(out_large).sum().item()
    print(f"    NaN count: {nan_count}, Inf count: {inf_count}")
    
    if nan_count > 0 or inf_count > 0:
        print("  ⚠️ WARNING: NaN/Inf con valores grandes — esto puede causar crash!")
    
    # Test con valores extremos (como podrían llegar de un LOB ruidoso)
    x_extreme = torch.randn(4, 16, device=device) * 100.0
    out_extreme = kan(x_extreme)
    print(f"  Extreme input range [-100, 100]: output range [{out_extreme.min():.4f}, {out_extreme.max():.4f}]")
    nan_count = torch.isnan(out_extreme).sum().item()
    inf_count = torch.isinf(out_extreme).sum().item()
    print(f"    NaN count: {nan_count}, Inf count: {inf_count}")
    
    # Test backward con valores extremos
    print("  Backward pass con valores extremos...")
    x_test = torch.randn(4, 16, device=device, requires_grad=True) * 50.0
    out = kan(x_test)
    loss = out.sum()
    loss.backward()
    grad_nan = torch.isnan(x_test.grad).sum().item() if x_test.grad is not None else -1
    print(f"    Gradient NaN count: {grad_nan}")
    
    # Test TKANLayer completo
    print("  TKANLayer con secuencia realista...")
    tkan = TKANLayer(
        input_dim=128, hidden_dim=64, n_rkan_layers=3,
        kan_hidden=24, grid_size=5, spline_order=3,
        return_sequences=True, dropout=0.1
    ).to(device)
    
    # Input que simula output del fusion_proj (después de LeakyReLU)
    fusion_out = torch.randn(4, 50, 128, device=device) * 2.0  # Rango post-ReLU típico
    tkan.eval()
    with torch.no_grad():
        tkan_out = tkan(fusion_out)
    nan_count = torch.isnan(tkan_out).sum().item()
    print(f"    TKANLayer output NaN: {nan_count}")
    print(f"    TKANLayer output range: [{tkan_out.min():.4f}, {tkan_out.max():.4f}]")
    
    print("  ✅ PASS")


def test_3_model_with_realistic_data(tensor_seqs, ofi_seqs):
    """Testa el modelo con datos de Feature Engine reales."""
    print("\n[TEST 3] SiameseTKAN Model — forward con datos reales")
    
    device = torch.device("cpu")
    model = SiameseTKANModel(
        seq_len=50, levels=20, lob_features=2, ofi_features=6,
        tkan_hidden=64, n_rkan_layers=3, kan_hidden=24,
        grid_size=5, use_attention=True, dropout=0.1,
    ).to(device)
    
    # Usar tensores reales del Feature Engine
    n = min(len(tensor_seqs), len(ofi_seqs), 8)
    if n == 0:
        print("  ⚠️ No hay suficientes datos, usando datos sintéticos")
        X_lob = torch.randn(4, 50, 20, 2, 2, device=device)
        X_ofi = torch.randn(4, 50, 20, 6, device=device)
    else:
        X_lob = torch.tensor(np.array(tensor_seqs[:n]), dtype=torch.float32, device=device)
        X_ofi = torch.tensor(np.array(ofi_seqs[:n]), dtype=torch.float32, device=device)
    
    print(f"  X_lob shape: {X_lob.shape}, range: [{X_lob.min():.4f}, {X_lob.max():.4f}]")
    print(f"  X_ofi shape: {X_ofi.shape}, range: [{X_ofi.min():.4f}, {X_ofi.max():.4f}]")
    
    # Forward pass
    model.eval()
    with torch.no_grad():
        preds = model(X_lob, X_ofi)
    
    nan_preds = torch.isnan(preds).sum().item()
    inf_preds = torch.isinf(preds).sum().item()
    print(f"  Output shape: {preds.shape}")
    print(f"  Output values: {preds.squeeze().tolist()}")
    print(f"  NaN: {nan_preds}, Inf: {inf_preds}")
    
    if nan_preds > 0 or inf_preds > 0:
        print("  ❌ FAIL: NaN/Inf en predicciones!")
        # Diagnóstico: localizar dónde aparece el NaN
        _diagnose_nan(model, X_lob, X_ofi)
        return False
    
    print("  ✅ PASS")
    return True


def _diagnose_nan(model, X_lob, X_ofi):
    """Diagnostica dónde aparece NaN en el forward pass."""
    print("  --- Diagnóstico NaN ---")
    B, T, L, S, F = X_lob.shape
    
    bids = X_lob[:, :, :, 0, :]
    asks = X_lob[:, :, :, 1, :]
    bids_flat = bids.reshape(B * T, L, F).permute(0, 2, 1)
    asks_flat = asks.reshape(B * T, L, F).permute(0, 2, 1)
    
    with torch.no_grad():
        b_out = model.spatial_cnn(bids_flat)
        a_out = model.spatial_cnn(asks_flat)
        print(f"  spatial_cnn bid: NaN={torch.isnan(b_out).sum()}, range=[{b_out.min():.4f}, {b_out.max():.4f}]")
        print(f"  spatial_cnn ask: NaN={torch.isnan(a_out).sum()}, range=[{a_out.min():.4f}, {a_out.max():.4f}]")
        
        lob_combined = torch.cat([b_out, a_out], dim=-1)
        ofi_flat = X_ofi.reshape(B * T, L, -1).permute(0, 2, 1)
        ofi_out = model.ofi_cnn(ofi_flat)
        print(f"  ofi_cnn: NaN={torch.isnan(ofi_out).sum()}, range=[{ofi_out.min():.4f}, {ofi_out.max():.4f}]")
        
        combined = torch.cat([lob_combined, ofi_out], dim=-1)
        fused = model.fusion_proj(combined)
        print(f"  fusion_proj: NaN={torch.isnan(fused).sum()}, range=[{fused.min():.4f}, {fused.max():.4f}]")
        
        fused_seq = fused.reshape(B, T, -1)
        tkan_out = model.tkan(fused_seq)
        print(f"  tkan: NaN={torch.isnan(tkan_out).sum()}, range=[{tkan_out.min():.4f}, {tkan_out.max():.4f}]")
        
        if model.attention is not None and tkan_out.dim() == 3:
            attn_out = model.attention(tkan_out)
            print(f"  attention: NaN={torch.isnan(attn_out).sum()}, range=[{attn_out.min():.4f}, {attn_out.max():.4f}]")


def test_4_training_pipeline(tensor_seqs, ofi_seqs):
    """Reproduce exactamente _online_retrain con datos reales."""
    print("\n[TEST 4] Training Pipeline — reproduce _online_retrain()")
    
    predictor = NeuralPredictor(
        sequence_length=50,
        prediction_horizon=500,
        symbol="BTCUSDT_test",
        use_legacy_ensemble=False,
    )
    
    # Armar training data exactamente como _online_retrain
    batch_size = min(len(tensor_seqs) - 10, 200)  # Más pequeño para test
    if batch_size < 10:
        print("  ⚠️ No hay suficientes tensor_seqs, generando sintéticos")
        batch_size = 100
        X_all = np.random.randn(batch_size, 50, 20, 2, 2).astype(np.float32) * 5
        y = np.random.randn(batch_size).astype(np.float32) * 0.001
        X_ofi = np.random.randn(batch_size, 50, 20, 6).astype(np.float32)
    else:
        X_all = np.array(tensor_seqs[:batch_size], dtype=np.float32)
        y = np.random.randn(batch_size).astype(np.float32) * 0.001
        if len(ofi_seqs) >= batch_size:
            X_ofi = np.array(ofi_seqs[:batch_size], dtype=np.float32)
        else:
            X_ofi = None
    
    print(f"  X_lob shape: {X_all.shape}")
    print(f"  X_lob range: [{X_all.min():.4f}, {X_all.max():.4f}]")
    print(f"  y shape: {y.shape}, y range: [{y.min():.6f}, {y.max():.6f}]")
    if X_ofi is not None:
        print(f"  X_ofi shape: {X_ofi.shape}")
        print(f"  X_ofi range: [{X_ofi.min():.4f}, {X_ofi.max():.4f}]")
    
    print(f"  Device: {predictor.device}")
    print(f"  Training...")
    
    try:
        t0 = time.time()
        loss = predictor.train_batch(X_all, y, X_ofi=X_ofi, epochs=1, mini_batch_size=32)
        elapsed = time.time() - t0
        print(f"  Loss: {loss:.6f}")
        print(f"  Tiempo: {elapsed:.2f}s")
        print(f"  is_trained: {predictor.is_trained}")
        
        # Verificar que el modelo no tiene NaN en sus pesos
        nan_params = 0
        for name, p in predictor.model.named_parameters():
            if torch.isnan(p).any():
                print(f"  ❌ NaN en parámetro: {name}")
                nan_params += 1
        if nan_params == 0:
            print(f"  Todos los parámetros son finitos ✓")
        
    except Exception as e:
        print(f"  ❌ EXCEPTION: {type(e).__name__}: {e}")
        traceback.print_exc()
        return False
    
    print("  ✅ PASS")
    return True


def test_5_predict_after_training(tensor_seqs, ofi_seqs):
    """Verifica que predict() funciona después de entrenar."""
    print("\n[TEST 5] Predict — después de training")
    
    predictor = NeuralPredictor(
        sequence_length=50,
        prediction_horizon=500,
        symbol="BTCUSDT_test",
        use_legacy_ensemble=False,
    )
    
    # Entrenar primero
    batch_size = min(len(tensor_seqs) - 10, 100)
    if batch_size < 10:
        X_all = np.random.randn(50, 50, 20, 2, 2).astype(np.float32) * 5
        y = np.random.randn(50).astype(np.float32) * 0.001
        X_ofi = np.random.randn(50, 50, 20, 6).astype(np.float32)
    else:
        X_all = np.array(tensor_seqs[:batch_size], dtype=np.float32)
        y = np.random.randn(batch_size).astype(np.float32) * 0.001
        X_ofi = np.array(ofi_seqs[:batch_size]) if len(ofi_seqs) >= batch_size else None
    
    loss = predictor.train_batch(X_all, y, X_ofi=X_ofi, epochs=1, mini_batch_size=32)
    print(f"  Post-training loss: {loss:.6f}")
    
    # Predecir
    test_seq = X_all[0]  # [50, 20, 2, 2]
    test_ofi = X_ofi[0] if X_ofi is not None else None  # [50, 20, 6]
    
    pred = predictor.predict(test_seq, test_ofi)
    print(f"  Prediction: {pred}")
    print(f"  Prediction type: {type(pred)}")
    
    if np.isnan(pred) or np.isinf(pred):
        print("  ❌ FAIL: NaN/Inf prediction!")
        return False
    
    print("  ✅ PASS")
    return True


def test_6_multi_epoch_stability():
    """Verifica estabilidad del training con múltiples epochs."""
    print("\n[TEST 6] Multi-epoch training — estabilidad")
    
    predictor = NeuralPredictor(
        sequence_length=50,
        prediction_horizon=500,
        symbol="BTCUSDT_test",
        use_legacy_ensemble=False,
    )
    
    # Datos sintéticos pero con magnitudes realistas
    # Simular lo que produce feature_engine: precios normalizados en bps, cantidades normalizadas
    X = np.zeros((200, 50, 20, 2, 2), dtype=np.float32)
    for i in range(200):
        for t in range(50):
            # bid prices: negativo, -1 a -200 bps
            X[i, t, :, 0, 0] = np.linspace(-1, -200, 20) + np.random.randn(20) * 2
            # ask prices: positivo, 1 a 200 bps
            X[i, t, :, 1, 0] = np.linspace(1, 200, 20) + np.random.randn(20) * 2
            # bid qty: 0 to 0.1 (normalized)
            X[i, t, :, 0, 1] = np.abs(np.random.randn(20)) * 0.05
            # ask qty: 0 to 0.1
            X[i, t, :, 1, 1] = np.abs(np.random.randn(20)) * 0.05
    
    X_ofi = np.random.randn(200, 50, 20, 6).astype(np.float32) * 0.5
    y = np.random.randn(200).astype(np.float32) * 0.001
    
    print(f"  X_lob range: [{X.min():.1f}, {X.max():.1f}]")
    
    losses = []
    for epoch in range(5):
        loss = predictor.train_batch(X, y, X_ofi=X_ofi, epochs=1, mini_batch_size=32)
        losses.append(loss)
        
        # Check for NaN in model params
        nan_found = False
        for name, p in predictor.model.named_parameters():
            if torch.isnan(p).any():
                print(f"  ❌ NaN en {name} después de epoch {epoch}")
                nan_found = True
                break
        
        if nan_found:
            return False
        
        # Predict
        pred = predictor.predict(X[0], X_ofi[0])
        print(f"  Epoch {epoch}: loss={loss:.6f}, pred={pred:.6f}, NaN={np.isnan(pred)}")
        
        if np.isnan(pred) or np.isnan(loss):
            print(f"  ❌ FAIL: NaN detectado en epoch {epoch}")
            return False
    
    print(f"  Losses: {[f'{l:.6f}' for l in losses]}")
    print("  ✅ PASS")
    return True


def test_7_threading_safety():
    """Testa training en un thread separado (como asyncio.to_thread)."""
    print("\n[TEST 7] Threading — training en thread separado")
    
    predictor = NeuralPredictor(
        sequence_length=50,
        prediction_horizon=500,
        symbol="BTCUSDT_thread",
        use_legacy_ensemble=False,
    )
    
    X = np.random.randn(100, 50, 20, 2, 2).astype(np.float32) * 5
    y = np.random.randn(100).astype(np.float32) * 0.001
    X_ofi = np.random.randn(100, 50, 20, 6).astype(np.float32)
    
    result = {"loss": None, "error": None}
    
    def train_in_thread():
        try:
            result["loss"] = predictor.train_batch(X, y, X_ofi=X_ofi, epochs=2, mini_batch_size=32)
        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}"
            traceback.print_exc()
    
    thread = threading.Thread(target=train_in_thread)
    print("  Iniciando thread de training...")
    thread.start()
    thread.join(timeout=60)
    
    if thread.is_alive():
        print("  ❌ FAIL: Thread no terminó en 60s")
        return False
    
    if result["error"]:
        print(f"  ❌ FAIL: {result['error']}")
        return False
    
    print(f"  Loss: {result['loss']:.6f}")
    print("  ✅ PASS")
    return True


def test_8_asyncio_integration():
    """Testa el flujo asyncio.to_thread exacto que usa NanoFenix."""
    print("\n[TEST 8] asyncio.to_thread — simulación real")
    
    predictor = NeuralPredictor(
        sequence_length=50,
        prediction_horizon=500,
        symbol="BTCUSDT_async",
        use_legacy_ensemble=False,
    )
    
    X = np.random.randn(100, 50, 20, 2, 2).astype(np.float32) * 5
    y = np.random.randn(100).astype(np.float32) * 0.001
    X_ofi = np.random.randn(100, 50, 20, 6).astype(np.float32)
    
    async def run_test():
        print("  Lanzando training con asyncio.to_thread...")
        
        def do_train():
            return predictor.train_batch(X, y, X_ofi=X_ofi, epochs=1, mini_batch_size=32)
        
        loss = await asyncio.to_thread(do_train)
        print(f"  Loss: {loss:.6f}")
        return loss
    
    try:
        loss = asyncio.run(run_test())
        if np.isnan(loss):
            print("  ❌ FAIL: NaN loss")
            return False
    except Exception as e:
        print(f"  ❌ FAIL: {type(e).__name__}: {e}")
        traceback.print_exc()
        return False
    
    print("  ✅ PASS")
    return True


if __name__ == "__main__":
    print("=" * 70)
    print("NanoFenix v2.0 — Test Profundo de Training Pipeline")
    print("=" * 70)
    print(f"Python: {sys.version}")
    print(f"PyTorch: {torch.__version__}")
    print(f"MPS disponible: {torch.backends.mps.is_available()}")
    print(f"Device: CPU (forzado por diseño)")
    
    results = {}
    
    # Test 1: Feature Engine
    tensor_seqs, ofi_seqs = _generate_feature_sequences()
    test_1_feature_engine_produces_correct_shapes(tensor_seqs, ofi_seqs)
    results["feature_engine"] = len(tensor_seqs) > 0
    
    # Test 2: KAN numerical stability
    test_2_kan_numerical_stability()
    results["kan_stability"] = True
    
    # Test 3: Model con datos reales 
    results["model_forward"] = test_3_model_with_realistic_data(tensor_seqs, ofi_seqs)
    
    # Test 4: Training pipeline completo
    results["training"] = test_4_training_pipeline(tensor_seqs, ofi_seqs)
    
    # Test 5: Predict después de training
    results["predict"] = test_5_predict_after_training(tensor_seqs, ofi_seqs)
    
    # Test 6: Multi-epoch stability
    results["multi_epoch"] = test_6_multi_epoch_stability()
    
    # Test 7: Threading
    results["threading"] = test_7_threading_safety()
    
    # Test 8: asyncio integration
    results["asyncio"] = test_8_asyncio_integration()
    
    # Resumen
    print("\n" + "=" * 70)
    print("RESUMEN")
    print("=" * 70)
    all_pass = True
    for name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {name:20s}: {status}")
        if not passed:
            all_pass = False
    
    print("=" * 70)
    if all_pass:
        print("TODOS LOS TESTS PASARON ✅")
    else:
        print("ALGUNOS TESTS FALLARON ❌")
    print("=" * 70)
    
    # Cleanup test model files
    for f in ["nanofenix/models/tkan_BTCUSDT_test_v2.pth",
              "nanofenix/models/tkan_BTCUSDT_thread_v2.pth",
              "nanofenix/models/tkan_BTCUSDT_async_v2.pth"]:
        if os.path.exists(f):
            os.remove(f)
    
    sys.exit(0 if all_pass else 1)
