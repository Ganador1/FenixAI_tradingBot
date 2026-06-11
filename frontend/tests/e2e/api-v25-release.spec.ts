import { test, expect } from '@playwright/test';

const API = process.env.FENIX_API_URL || 'http://127.0.0.1:8765';

/**
 * v2.5 endpoint contract tests.
 *
 * These hit the FastAPI server directly. The dashboard depends on the same
 * payloads, so if they regress the UI breaks. The server must be running.
 */
test.describe('v2.5 API contract', () => {
  test('GET /api/v25/release-info returns the recommended config', async ({ request }) => {
    const res = await request.get(`${API}/api/v25/release-info`);
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body.version).toBe('2.5.0');
    expect(body.recommended_symbol).toBe('SOLUSDT');
    expect(body.recommended_team).toMatchObject({
      technical: expect.any(String),
      qabba: expect.any(String),
      decision: expect.any(String),
      risk_manager: expect.any(String),
    });
    expect(body.nanofenix.default_observer_only).toBe(true);
    expect(Array.isArray(body.nanofenix.hard_veto_reasons)).toBe(true);
    expect(body.nanofenix.hard_veto_reasons).toContain('direction_mismatch');
    expect(body.nanofenix.hard_veto_reasons).toContain('high_uncertainty');
  });

  test('GET /api/nanofenix/status returns a structured payload', async ({ request }) => {
    const res = await request.get(`${API}/api/nanofenix/status?symbol=SOLUSDT`);
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body.symbol).toBe('SOLUSDT');
    expect(typeof body.running).toBe('boolean');
  });

  test('NanoFenix lifecycle: start -> status -> signal -> stop', async ({ request }) => {
    test.setTimeout(45_000);

    const startRes = await request.post(`${API}/api/nanofenix/start`, {
      data: { symbol: 'BTCUSDT', observer_only: true, adaptive_fusion: true },
    });
    expect(startRes.ok()).toBeTruthy();
    const start = await startRes.json();
    expect(start.running).toBe(true);
    expect(typeof start.pid).toBe('number');

    // Wait up to 12s for the companion to write its first signal.
    let signal: Record<string, unknown> | null = null;
    for (let i = 0; i < 12; i++) {
      await new Promise((r) => setTimeout(r, 1000));
      const sigRes = await request.get(`${API}/api/nanofenix/signal?symbol=BTCUSDT`);
      if (sigRes.ok()) {
        signal = await sigRes.json();
        if (signal?.timestamp_utc) break;
      }
    }
    expect(signal).not.toBeNull();
    expect(signal!.symbol).toBe('BTCUSDT');
    expect(signal!.timestamp_utc).toBeTruthy();
    // NanoFenix v3.5 emits position vocabulary (LONG/SHORT/FLAT/HOLD);
    // older builds used order vocabulary (BUY/SELL).
    expect(['LONG', 'SHORT', 'FLAT', 'HOLD', 'BUY', 'SELL']).toContain(signal!.signal);

    const stopRes = await request.post(`${API}/api/nanofenix/stop?symbol=BTCUSDT`, {
      timeout: 20_000,
    });
    expect(stopRes.ok()).toBeTruthy();
    const stop = await stopRes.json();
    expect(stop.stopped).toBeTruthy();
  });

  test('GET /api/system/health reports engine + binance components', async ({ request }) => {
    const res = await request.get(`${API}/api/system/health`);
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(Array.isArray(body.components)).toBe(true);
    const names = body.components.map((c: { component: string }) => c.component);
    expect(names).toContain('engine');
    expect(names).toContain('binance');
  });
});
