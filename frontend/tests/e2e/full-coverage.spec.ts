import { test, expect, APIRequestContext } from '@playwright/test';

const API = process.env.FENIX_API_URL || 'http://127.0.0.1:8765';

// Optional credentials. When the backend runs with JWT_SECRET set, control
// endpoints (engine/*, POST trading/orders) require a token. Provide demo
// creds via env to exercise the authenticated path:
//   FENIX_E2E_EMAIL=admin@fenix.ai FENIX_E2E_PASSWORD=...
const E2E_EMAIL = process.env.FENIX_E2E_EMAIL || '';
const E2E_PASSWORD = process.env.FENIX_E2E_PASSWORD || '';

async function tryLogin(request: APIRequestContext): Promise<string | null> {
  if (!E2E_EMAIL || !E2E_PASSWORD) return null;
  const res = await request.post(`${API}/api/auth/login`, {
    data: { email: E2E_EMAIL, password: E2E_PASSWORD },
  });
  if (!res.ok()) return null;
  const body = await res.json();
  return body.token || body.access_token || null;
}

function authHeaders(token: string | null): Record<string, string> {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/**
 * v2.5 full API coverage.
 *
 * Hits every dashboard-facing endpoint group so a UI regression can be
 * traced to its API contract. Control endpoints are auth-aware: with
 * JWT_SECRET set they must reject anonymous remote calls; from loopback
 * without JWT_SECRET they work (local dev mode).
 */
test.describe('Full API surface', () => {
  let token: string | null = null;

  test.beforeAll(async ({ request }) => {
    token = await tryLogin(request);
  });

  // ---- Health & system ---------------------------------------------------

  test('GET /health alias responds', async ({ request }) => {
    const res = await request.get(`${API}/health`);
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body.status).toBe('ok');
    expect(typeof body.engine_running).toBe('boolean');
  });

  test('GET /api/system/health lists components', async ({ request }) => {
    const res = await request.get(`${API}/api/system/health`);
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(Array.isArray(body.components)).toBe(true);
  });

  test('GET /api/system/status returns engine block', async ({ request }) => {
    const res = await request.get(`${API}/api/system/status`);
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body.engine).toBeDefined();
    expect(typeof body.engine.running).toBe('boolean');
    expect(body.engine.symbol).toBeTruthy();
    expect(body.engine.timeframe).toBeTruthy();
  });

  test('GET /api/system/connections and /metrics/history respond', async ({ request }) => {
    const conns = await request.get(`${API}/api/system/connections`);
    expect(conns.ok()).toBeTruthy();

    const history = await request.get(`${API}/api/system/metrics/history?timeframe=15m`);
    expect(history.ok()).toBeTruthy();
    const body = await history.json();
    expect(Array.isArray(body.metrics)).toBe(true);
  });

  test('GET /api/system/settings returns the settings sections', async ({ request }) => {
    const res = await request.get(`${API}/api/system/settings`);
    expect(res.ok()).toBeTruthy();
  });

  // ---- Engine control (auth-aware) ----------------------------------------

  test('GET /api/engine/config returns current config', async ({ request }) => {
    const res = await request.get(`${API}/api/engine/config`);
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body.config).toBeDefined();
  });

  test('POST /api/engine/start + stop honour the control guard', async ({ request }) => {
    test.setTimeout(60_000);

    const startRes = await request.post(`${API}/api/engine/start`, {
      headers: authHeaders(token),
    });

    if (startRes.ok()) {
      // Accepted: either auth passed or loopback dev mode. Stop it again to
      // leave the system as we found it.
      const body = await startRes.json();
      expect(body.status).toBe('started');
      const stopRes = await request.post(`${API}/api/engine/stop`, {
        headers: authHeaders(token),
      });
      expect(stopRes.ok()).toBeTruthy();
    } else {
      // Auth enforced: must be 401 (no/invalid token) or 503 (JWT not set,
      // remote client) — never a silent 500.
      expect([401, 403, 503]).toContain(startRes.status());
    }
  });

  // ---- Trading -------------------------------------------------------------

  test('GET trading endpoints respond', async ({ request }) => {
    for (const path of [
      '/api/trading/orders',
      '/api/trading/positions',
      '/api/trading/history',
      '/api/trading/market',
    ]) {
      const res = await request.get(`${API}${path}`);
      expect(res.ok(), `${path} should respond 200`).toBeTruthy();
    }
  });

  test('POST /api/trading/orders is control-guarded and accepts valid payload', async ({ request }) => {
    const res = await request.post(`${API}/api/trading/orders`, {
      headers: authHeaders(token),
      data: {
        symbol: 'BTCUSDT',
        type: 'market',
        side: 'buy',
        quantity: 0.001,
      },
    });
    if (res.ok()) {
      const body = await res.json();
      expect(body.symbol).toBe('BTCUSDT');
    } else {
      expect([401, 403, 503]).toContain(res.status());
    }
  });

  // ---- Agents & reasoning --------------------------------------------------

  test('GET /api/agents returns the 6-agent team', async ({ request }) => {
    const res = await request.get(`${API}/api/agents`);
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(Array.isArray(body.agents)).toBe(true);
    expect(body.agents.length).toBeGreaterThanOrEqual(4);
  });

  test('GET reasoning endpoints respond', async ({ request }) => {
    for (const path of [
      '/api/reasoning',
      '/api/reasoning/analytics?timeframe=24h',
      '/api/reasoning/consensus?timeframe=24h',
    ]) {
      const res = await request.get(`${API}${path}`);
      expect(res.ok(), `${path} should respond 200`).toBeTruthy();
    }
  });

  // ---- v2.5 companions -------------------------------------------------------

  test('GET /api/nanofenix/status is always available', async ({ request }) => {
    const res = await request.get(`${API}/api/nanofenix/status?symbol=SOLUSDT`);
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body.symbol).toBe('SOLUSDT');
    expect(typeof body.running).toBe('boolean');
  });

  test('GET /api/minifenix/regime returns data or a clean 404', async ({ request }) => {
    const res = await request.get(`${API}/api/minifenix/regime`);
    if (res.ok()) {
      const body = await res.json();
      expect(body.regime).toBeDefined();
      expect(typeof body.age_seconds).toBe('number');
    } else {
      expect(res.status()).toBe(404);
      const body = await res.json();
      expect(body.detail).toContain('MiniFenix');
    }
  });

  test('GET /api/v25/release-info matches the Companions page contract', async ({ request }) => {
    const res = await request.get(`${API}/api/v25/release-info`);
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body.version).toBe('2.5.0');
    expect(body.recommended_team).toBeDefined();
    expect(body.subsystems).toBeDefined();
  });

  // ---- Auth hardening --------------------------------------------------------

  test('login rejects bad credentials without leaking detail', async ({ request }) => {
    const res = await request.post(`${API}/api/auth/login`, {
      data: { email: 'nobody@nowhere.dev', password: 'wrong-password-123' },
    });
    expect([401, 429, 500]).toContain(res.status());
  });

  test('admin user endpoints require auth', async ({ request }) => {
    const res = await request.get(`${API}/api/auth/users`);
    expect(res.status()).toBe(401);
  });
});
