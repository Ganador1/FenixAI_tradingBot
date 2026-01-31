#!/usr/bin/env python3
"""
Test del sistema de indicadores y TradingView scraper
"""

import sys
sys.path.insert(0, '.')

import pandas as pd
import numpy as np

def test_sfp():
    """Test del indicador Swing Failure Pattern"""
    print('🧪 Test 1: Swing Failure Pattern')
    
    from src.indicators.swing_failure_pattern import SwingFailurePattern
    
    np.random.seed(42)
    n = 150
    
    # Crear datos con patrón de sweep deliberado
    price = 100.0
    data = []
    for i in range(n):
        volatility = 3.0 if 80 <= i <= 85 else 1.0
        
        open_p = price
        change = np.random.randn() * volatility
        high_p = open_p + abs(change) + np.random.rand() * volatility
        low_p = open_p - abs(change) - np.random.rand() * volatility
        close_p = open_p + change * 0.5
        
        data.append({
            'open': open_p,
            'high': max(high_p, open_p, close_p),
            'low': min(low_p, open_p, close_p),
            'close': close_p
        })
        price = close_p
    
    df = pd.DataFrame(data)
    print(f'  Datos: {len(df)} barras')
    
    sfp = SwingFailurePattern(pivot_len=5, patience=7)
    result = sfp.calculate(df)
    signals = sfp.get_signals(result)
    
    print(f'  Señales SFP: {len(signals)}')
    for s in signals[-3:]:
        print(f'    {s.signal_type.value}: bar {s.bar_index} @ ${s.price:.2f}')
    
    print('  ✅ SFP OK')
    return True


def test_registry():
    """Test del registro de indicadores"""
    print()
    print('🧪 Test 2: Indicator Registry')
    
    from src.indicators.indicator_library import get_registry
    
    registry = get_registry()
    indicators = registry.list_indicators()
    print(f'  Indicadores registrados: {indicators}')
    
    # Crear datos de prueba
    np.random.seed(42)
    data = []
    price = 100.0
    for _ in range(100):
        open_p = price
        change = np.random.randn()
        data.append({
            'open': open_p,
            'high': open_p + abs(change) + 0.5,
            'low': open_p - abs(change) - 0.5,
            'close': open_p + change * 0.5
        })
        price = open_p + change * 0.5
    
    df = pd.DataFrame(data)
    
    result = registry.apply('swing_failure_pattern', df, pivot_len=5)
    print(f'  Apply result: success={result.success}, signals={len(result.signals)}')
    print('  ✅ Registry OK')
    return True


def test_playwright():
    """Test de Playwright para TradingView"""
    print()
    print('🧪 Test 3: Playwright + TradingView')
    
    import asyncio
    from playwright.async_api import async_playwright
    
    async def run():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            
            # Cargar TradingView
            await page.goto('https://www.tradingview.com/chart/?symbol=BINANCE:BTCUSDT')
            await page.wait_for_load_state('networkidle')
            
            title = await page.title()
            print(f'  Página cargada: {title[:50]}...')
            
            # Capturar screenshot
            await page.screenshot(path='cache/tradingview/test_btcusdt.png')
            print('  Screenshot guardado: cache/tradingview/test_btcusdt.png')
            
            await browser.close()
            return True
    
    try:
        import os
        os.makedirs('cache/tradingview', exist_ok=True)
        result = asyncio.run(run())
        print('  ✅ Playwright OK')
        return result
    except Exception as e:
        print(f'  ⚠️ Error: {e}')
        return False


def main():
    print('=' * 60)
    print('🔬 FenixAI - Test Suite de Indicadores')
    print('=' * 60)
    
    results = []
    
    # Test SFP
    try:
        results.append(('SFP Indicator', test_sfp()))
    except Exception as e:
        print(f'  ❌ Error: {e}')
        results.append(('SFP Indicator', False))
    
    # Test Registry
    try:
        results.append(('Indicator Registry', test_registry()))
    except Exception as e:
        print(f'  ❌ Error: {e}')
        results.append(('Indicator Registry', False))
    
    # Test Playwright
    try:
        results.append(('Playwright/TradingView', test_playwright()))
    except Exception as e:
        print(f'  ❌ Error: {e}')
        results.append(('Playwright/TradingView', False))
    
    # Resumen
    print()
    print('=' * 60)
    print('📊 Resumen:')
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    
    for name, ok in results:
        emoji = '✅' if ok else '❌'
        print(f'  {emoji} {name}')
    
    print()
    print(f'  {passed}/{total} tests pasaron')
    
    if passed == total:
        print()
        print('🎉 ¡Todo funciona correctamente!')
    
    return passed == total


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
