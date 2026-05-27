#!/usr/bin/env python3
"""Validation tests for NanoFenix v2.0 T-KAN implementation."""

import sys
import torch
import numpy as np

def test_device():
    print(f"Python: {sys.version}")
    print(f"PyTorch: {torch.__version__}")
    print(f"MPS disponible: {torch.backends.mps.is_available()}")
    print(f"MPS built: {torch.backends.mps.is_built()}")
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    print(f"Device seleccionado: {device}")
    assert device.type in {"cpu", "mps", "cuda"}


def test_kan_layers(device):
    from nanofenix.kan_layers import BSplineKANLayer, RKANLayer, TKANCell, TKANLayer, CompactSelfAttention

    # BSplineKANLayer
    kan = BSplineKANLayer(16, 8, grid_size=5, spline_order=3).to(device)
    x = torch.randn(4, 16, device=device)
    out = kan(x)
    assert out.shape == (4, 8), f"BSplineKAN shape error: {out.shape}"
    print(f"  BSplineKANLayer(16→8): params={sum(p.numel() for p in kan.parameters()):,}")

    # RKANLayer (input_dim, kan_input, kan_output)
    rkan = RKANLayer(16, 12, 8, grid_size=5, spline_order=3).to(device)
    h = torch.zeros(4, 8, device=device)
    out, h_new = rkan(x, h)
    assert out.shape == (4, 8), f"RKAN output shape error: {out.shape}"
    assert h_new.shape == (4, 8), f"RKAN hidden shape error: {h_new.shape}"
    print(f"  RKANLayer(16→12→8): OK")

    # TKANCell
    cell = TKANCell(16, 32, n_rkan_layers=3, grid_size=5, spline_order=3).to(device)
    h = torch.zeros(4, 32, device=device)
    c = torch.zeros(4, 32, device=device)
    h_new, c_new, rkan_states = cell(x, h, c)
    assert h_new.shape == (4, 32), f"TKANCell output shape error: {h_new.shape}"
    print(f"  TKANCell(16→32, 3 sub-layers): OK")

    # TKANLayer (uses n_rkan_layers, return_sequences for full seq output)
    tkan = TKANLayer(16, 32, n_rkan_layers=3, return_sequences=True).to(device)
    seq = torch.randn(4, 30, 16, device=device)
    out = tkan(seq)
    assert out.shape == (4, 30, 32), f"TKANLayer shape error: {out.shape}"
    print(f"  TKANLayer(16→32, 2 layers, seq=30): output={out.shape}")

    # CompactSelfAttention (hidden_dim, dropout)
    attn = CompactSelfAttention(32, dropout=0.0).to(device)
    out = attn(torch.randn(4, 30, 32, device=device))
    assert out.shape == (4, 32), f"Attention shape error: {out.shape}"
    print(f"  CompactSelfAttention(32, seq=30): OK")

    print("✅ Todas las KAN layers validadas")


def test_deep_ofi():
    from nanofenix.deep_ofi_engine import DeepOFIEngine

    engine = DeepOFIEngine(max_levels=20, lookback=50)

    result = None
    # Simular 100 ticks de LOB @depth20
    for i in range(100):
        mid = 50000.0 + np.random.randn() * 10
        bid_prices = np.array([mid - j * 0.5 for j in range(1, 21)])
        bid_qtys = np.array([100 + np.random.rand() * 50 for _ in range(20)])
        ask_prices = np.array([mid + j * 0.5 for j in range(1, 21)])
        ask_qtys = np.array([100 + np.random.rand() * 50 for _ in range(20)])
        result = engine.update(bid_prices, bid_qtys, ask_prices, ask_qtys, mid)

    assert result is not None, "DeepOFI devolvio None despues de 100 ticks"
    scalar_features, tensor_features = result
    assert scalar_features.shape == (8,), f"Scalar shape incorrecto: {scalar_features.shape}"
    assert tensor_features.shape == (20, 6), f"Tensor shape incorrecto: {tensor_features.shape}"

    snap = engine.get_snapshot()
    print(f"  weighted_ofi: {snap.weighted_ofi:.4f}")
    print(f"  absorption_ratio: {snap.absorption_ratio:.4f}")
    print(f"  scalar_features shape: {scalar_features.shape}")
    print(f"  tensor shape: {tensor_features.shape}")
    print("✅ Deep OFI Engine validado")


def test_siamese_tkan_model(device):
    from nanofenix.neural_predictor import SiameseTKANModel

    model = SiameseTKANModel(
        levels=20,
        lob_features=2,
        ofi_features=6,
        tkan_hidden=64,
        n_rkan_layers=3,
        grid_size=5,
        seq_len=30,
        dropout=0.1,
    ).to(device)

    total_params = sum(p.numel() for p in model.parameters())
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"  Params totales: {total_params:,}")
    print(f"  Params entrenables: {trainable:,}")
    print(f"  Tamano modelo: {total_params * 4 / 1024:.1f} KB (FP32)")

    # Forward pass
    B, T = 4, 30
    lob_tensor = torch.randn(B, T, 20, 2, 2, device=device)
    ofi_tensor = torch.randn(B, T, 20, 6, device=device)

    model.eval()
    with torch.no_grad():
        pred = model(lob_tensor, ofi_tensor)

    assert pred.shape == (B, 1), f"Output shape incorrecto: {pred.shape}"
    print(f"  Input LOB: {lob_tensor.shape}")
    print(f"  Input OFI: {ofi_tensor.shape}")
    print(f"  Output: {pred.shape}")
    print(f"  Predictions: {pred.squeeze().tolist()}")
    print("✅ SiameseTKANModel forward pass exitoso")


def test_training_step(device):
    from nanofenix.neural_predictor import SiameseTKANModel
    
    model = SiameseTKANModel(
        levels=20, lob_features=2, ofi_features=6,
        tkan_hidden=64, n_rkan_layers=3,
        seq_len=30, dropout=0.1,
    ).to(device)
    
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-4)
    loss_fn = torch.nn.HuberLoss(delta=0.001)
    
    B, T = 8, 30
    lob = torch.randn(B, T, 20, 2, 2, device=device)
    ofi = torch.randn(B, T, 20, 6, device=device)
    target = torch.randn(B, 1, device=device) * 0.01
    
    model.train()
    pred = model(lob, ofi)
    loss = loss_fn(pred, target)
    loss.backward()
    
    # Check gradients exist
    grad_count = sum(1 for p in model.parameters() if p.grad is not None)
    total_count = sum(1 for p in model.parameters())
    
    optimizer.step()
    optimizer.zero_grad()
    
    print(f"  Loss: {loss.item():.6f}")
    print(f"  Gradients: {grad_count}/{total_count} params have gradients")
    print("✅ Training step exitoso (backward + optimizer step)")


def test_neural_predictor_controller(device):
    from nanofenix.neural_predictor import NeuralPredictor

    predictor = NeuralPredictor(
        symbol="BTCUSDT",
        sequence_length=30,
        use_legacy_ensemble=False,
    )

    print(f"  Device: {predictor.device}")
    print(f"  Model type: {type(predictor.model).__name__}")
    print(f"  Params: {predictor.model.count_parameters():,}")
    print(f"  Trained: {predictor.is_trained}")
    print("✅ NeuralPredictor controller instanciado")


if __name__ == "__main__":
    print("=" * 60)
    print("NanoFenix v2.0 - Validacion T-KAN + Deep OFI")
    print("=" * 60)

    print("\n[1/6] Dispositivo...")
    test_device()
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")

    print("\n[2/6] KAN Layers...")
    test_kan_layers(device)

    print("\n[3/6] Deep OFI Engine...")
    test_deep_ofi()

    print("\n[4/6] SiameseTKAN Model...")
    test_siamese_tkan_model(device)

    print("\n[5/6] Training Step...")
    test_training_step(device)

    print("\n[6/6] NeuralPredictor Controller...")
    test_neural_predictor_controller(device)

    print("\n" + "=" * 60)
    print("TODOS LOS TESTS PASARON ✅")
    print("=" * 60)
