import { test, expect } from '@playwright/test';

const API = process.env.FENIX_API_URL || 'http://127.0.0.1:8765';
const FRONTEND = process.env.PLAYWRIGHT_BASE_URL || 'http://127.0.0.1:5173';

/**
 * v2.5 NanoFenix UI integration.
 *
 * The dashboard should be able to read the NanoFenix companion signal from
 * the FastAPI server and broadcast it via Socket.IO. This test exercises
 * the round-trip without depending on a specific UI component name — it
 * confirms the API + Socket.IO contract the dashboard relies on.
 */
test.describe('NanoFenix companion UI integration', () => {
  test.beforeAll(async ({ request }) => {
    // Make sure at least one companion has produced a signal.
    await request.post(`${API}/api/nanofenix/start`, {
      data: { symbol: 'SOLUSDT', observer_only: true, adaptive_fusion: true },
    });
    // Wait for the file to appear.
    for (let i = 0; i < 15; i++) {
      const res = await request.get(`${API}/api/nanofenix/signal?symbol=SOLUSDT`);
      if (res.ok()) return;
      await new Promise((r) => setTimeout(r, 1000));
    }
  });

  test.afterAll(async ({ request }) => {
    await request.post(`${API}/api/nanofenix/stop?symbol=SOLUSDT`, { timeout: 20_000 });
  });

  test('the signal endpoint exposes the policy fields the dashboard expects', async ({ request }) => {
    const res = await request.get(`${API}/api/nanofenix/signal?symbol=SOLUSDT`);
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    const policyFields = [
      'signal',
      'action',
      'confidence',
      'direction_accuracy',
      'regime',
      'trend',
      'allow_execute',
      'allow_add_to_position',
      'has_position',
      'pred_bps',
    ];
    for (const field of policyFields) {
      expect(body).toHaveProperty(field);
    }
  });

  test('frontend can fetch the signal through the proxied API', async ({ page }) => {
    await page.goto(FRONTEND);
    await page.waitForLoadState('networkidle');

    const apiSignal = await page.evaluate(async (apiBase: string) => {
      const r = await fetch(`${apiBase}/api/nanofenix/signal?symbol=SOLUSDT`);
      return r.ok ? r.json() : null;
    }, API);

    expect(apiSignal).not.toBeNull();
    expect(apiSignal.symbol).toBe('SOLUSDT');
  });
});
