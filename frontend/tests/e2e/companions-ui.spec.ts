import { test, expect, Page } from '@playwright/test';

const FRONTEND = process.env.PLAYWRIGHT_BASE_URL || 'http://127.0.0.1:5173';
const E2E_EMAIL = process.env.FENIX_E2E_EMAIL || '';
const E2E_PASSWORD = process.env.FENIX_E2E_PASSWORD || '';

/**
 * Companions page UI (NanoFenix / MiniFenix / v2.5 release info).
 *
 * Requires the backend on 127.0.0.1:8765 and login credentials via
 * FENIX_E2E_EMAIL / FENIX_E2E_PASSWORD (demo users: CREATE_DEMO_USERS=true).
 * Tests are skipped gracefully when credentials are not provided.
 */

async function login(page: Page): Promise<boolean> {
  await page.goto(`${FRONTEND}/login`);
  await page.waitForLoadState('networkidle');

  const emailInput = page.locator('input[type="email"], input[name="email"]').first();
  const passwordInput = page.locator('input[type="password"]').first();
  if ((await emailInput.count()) === 0) return false;

  await emailInput.fill(E2E_EMAIL);
  await passwordInput.fill(E2E_PASSWORD);
  await page.locator('button[type="submit"]').first().click();

  try {
    await page.waitForURL(/dashboard|companions|\/$/, { timeout: 15_000 });
    return true;
  } catch {
    return false;
  }
}

test.describe('Companions page', () => {
  test.skip(!E2E_EMAIL || !E2E_PASSWORD, 'Set FENIX_E2E_EMAIL / FENIX_E2E_PASSWORD to run UI tests');

  test.beforeEach(async ({ page }) => {
    const ok = await login(page);
    expect(ok, 'login should succeed with the provided credentials').toBeTruthy();
  });

  test('sidebar exposes the Companions link with the v2.5 badge', async ({ page }) => {
    await page.goto(`${FRONTEND}/dashboard`);
    await page.waitForLoadState('networkidle');

    const link = page.locator('a[href="/companions"]');
    await expect(link).toBeVisible();
    await expect(link).toContainText(/companions/i);
    await expect(link).toContainText(/v2\.5/i);
  });

  test('renders all the Companions sections', async ({ page }) => {
    await page.goto(`${FRONTEND}/companions`);
    await page.waitForLoadState('networkidle');

    await expect(page.getByTestId('companions-page')).toBeVisible();
    await expect(page.getByTestId('nano-control-card')).toBeVisible();
    await expect(page.getByTestId('nano-status-badge')).toBeVisible();
    await expect(page.getByTestId('mini-regime-card')).toBeVisible();
    await expect(page.getByTestId('release-info-card')).toBeVisible();

    // Signal panel shows either live data or the empty-state hint.
    const signalPanel = page.getByTestId('nano-signal-panel');
    const signalEmpty = page.getByTestId('nano-signal-empty');
    await expect(signalPanel.or(signalEmpty)).toBeVisible();

    // MiniFenix shows data or its prototype hint.
    const miniData = page.getByTestId('mini-regime-data');
    const miniEmpty = page.getByTestId('mini-regime-empty');
    await expect(miniData.or(miniEmpty)).toBeVisible();
  });

  test('release info card shows the recommended v2.5 team', async ({ page }) => {
    await page.goto(`${FRONTEND}/companions`);
    await page.waitForLoadState('networkidle');

    const card = page.getByTestId('release-info-card');
    await expect(card).toContainText(/SOLUSDT/);
    await expect(card).toContainText(/15m/);
    await expect(card).toContainText(/technical/i);
    await expect(card).toContainText(/decision/i);
    await expect(page.getByTestId('release-badge')).toContainText('2.5');
  });

  test('symbol selector and companion toggles are interactive', async ({ page }) => {
    await page.goto(`${FRONTEND}/companions`);
    await page.waitForLoadState('networkidle');

    const select = page.getByTestId('nano-symbol-select');
    await expect(select).toBeVisible();
    await select.selectOption('BTCUSDT');
    await expect(select).toHaveValue('BTCUSDT');
    await select.selectOption('SOLUSDT');

    const observer = page.getByTestId('nano-observer-toggle');
    await expect(observer).toBeChecked(); // safe default
    const fusion = page.getByTestId('nano-fusion-toggle');
    await expect(fusion).toBeChecked();
  });

  test('start/stop button reflects companion state and reacts to clicks', async ({ page }) => {
    test.setTimeout(90_000);
    await page.goto(`${FRONTEND}/companions`);
    await page.waitForLoadState('networkidle');

    const startBtn = page.getByTestId('nano-start-btn');
    const stopBtn = page.getByTestId('nano-stop-btn');

    if (await stopBtn.isVisible().catch(() => false)) {
      // Already running from a previous run: stop it.
      await stopBtn.click();
      await expect(page.getByTestId('nano-start-btn')).toBeVisible({ timeout: 30_000 });
      return;
    }

    await expect(startBtn).toBeVisible();
    await startBtn.click();

    // Either it starts (badge flips to RUNNING), or any toast appears (the
    // backend confirms or rejects the spawn), or the stop button shows up.
    const running = page.getByTestId('nano-status-badge').filter({ hasText: 'RUNNING' });
    const anyToast = page.locator('[data-sonner-toast]');
    const stopVisible = page.getByTestId('nano-stop-btn');
    await expect(running.or(anyToast).or(stopVisible).first()).toBeVisible({ timeout: 45_000 });

    // Cleanup if it actually started.
    if (await page.getByTestId('nano-stop-btn').isVisible().catch(() => false)) {
      await page.getByTestId('nano-stop-btn').click();
      await expect(page.getByTestId('nano-start-btn')).toBeVisible({ timeout: 30_000 });
    }
  });

  test('header shows the v2.5 brand and the engine toggle', async ({ page }) => {
    await page.goto(`${FRONTEND}/dashboard`);
    await page.waitForLoadState('networkidle');

    await expect(page.locator('header')).toContainText(/v2\.5/i);
    await expect(page.getByTestId('header-engine-status')).toBeVisible();
    await expect(page.getByTestId('engine-toggle-btn')).toBeVisible();
  });
});
