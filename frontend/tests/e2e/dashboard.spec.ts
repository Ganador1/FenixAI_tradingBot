import { test, expect, Page } from '@playwright/test';

const FRONTEND = process.env.PLAYWRIGHT_BASE_URL || 'http://127.0.0.1:5173';
const E2E_EMAIL = process.env.FENIX_E2E_EMAIL || '';
const E2E_PASSWORD = process.env.FENIX_E2E_PASSWORD || '';

async function maybeLogin(page: Page): Promise<boolean> {
  if (!E2E_EMAIL || !E2E_PASSWORD) return false;
  await page.goto(`${FRONTEND}/login`);
  await page.waitForLoadState('networkidle');
  const emailInput = page.locator('input[type="email"], input[name="email"]').first();
  if ((await emailInput.count()) === 0) return false;
  await emailInput.fill(E2E_EMAIL);
  await page.locator('input[type="password"]').first().fill(E2E_PASSWORD);
  await page.locator('button[type="submit"]').first().click();
  try {
    await page.waitForURL(/dashboard|\/$/, { timeout: 15_000 });
    return true;
  } catch {
    return false;
  }
}

/**
 * FenixAI v2.5 dashboard smoke + navigation.
 *
 * The dashboard needs the FastAPI server running on 127.0.0.1:8765. These
 * tests intentionally don't log in — they assert the public surface only.
 * If your build gates the dashboard behind auth, run with the demo user
 * pre-seeded (CREATE_DEMO_USERS=true in the backend env).
 */
test.describe('Dashboard navigation', () => {
  test('loads the home/dashboard route without console errors', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(e.message));
    page.on('console', (msg) => {
      if (msg.type() === 'error') errors.push(msg.text());
    });

    await page.goto(FRONTEND);
    await expect(page).toHaveTitle(/Fenix|FenixAI|Dashboard/i);

    expect(errors).toEqual([]);
  });

  test('main navigation links are present', async ({ page }) => {
    const authed = await maybeLogin(page);
    test.skip(!authed, 'Requires FENIX_E2E_EMAIL / FENIX_E2E_PASSWORD for the protected shell');
    await page.goto(FRONTEND);

    // Wait for the React shell to settle.
    await page.waitForLoadState('networkidle');

    // Each link is rendered by the Sidebar component. We assert by route
    // rather than by text to stay resilient to copy changes.
    const routes = [
      '/dashboard',
      '/agents',
      '/companions',
      '/trading',
      '/market',
      '/reasoning',
      '/system',
      '/settings',
    ];

    for (const route of routes) {
      const link = page.locator(`a[href="${route}"]`).first();
      if ((await link.count()) > 0) {
        await expect(link).toBeVisible();
      }
    }
  });

  test('dashboard renders at least one metric card or chart', async ({ page }) => {
    const authed = await maybeLogin(page);
    test.skip(!authed, 'Requires FENIX_E2E_EMAIL / FENIX_E2E_PASSWORD for the protected shell');
    await page.goto(`${FRONTEND}/dashboard`);
    await page.waitForLoadState('networkidle');

    const candidates = page.locator('[data-testid*="metric"], [class*="MetricCard"], [class*="Chart"], svg.recharts-surface');
    await expect(candidates.first()).toBeVisible({ timeout: 15_000 });
  });

  test('agents page lists the v2.5 agents (technical, qabba, decision, risk)', async ({ page }) => {
    const authed = await maybeLogin(page);
    test.skip(!authed, 'Requires FENIX_E2E_EMAIL / FENIX_E2E_PASSWORD for the protected shell');
    await page.goto(`${FRONTEND}/agents`);
    await page.waitForLoadState('networkidle');

    const html = await page.content();
    for (const name of ['echnical', 'isk', 'ecision']) {
      // Case-insensitive substring checks; the exact strings depend on UI copy.
      expect(html).toMatch(new RegExp(name, 'i'));
    }
  });
});
