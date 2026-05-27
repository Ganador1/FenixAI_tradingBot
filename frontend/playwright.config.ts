import { defineConfig, devices } from '@playwright/test';

/**
 * FenixAI v2.5 — Playwright end-to-end test config.
 *
 * These tests assume the FastAPI backend is up on 127.0.0.1:8765 and the
 * Vite dev server is up on 127.0.0.1:5173. Both can be started via the
 * project's existing scripts before running `npx playwright test`.
 *
 *   .venv/bin/python -m uvicorn src.api.server:app_socketio \
 *       --host 127.0.0.1 --port 8765
 *   cd frontend && npm run client:dev
 *
 * The `webServer` block lets Playwright boot the Vite server itself.
 */
export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: [['list'], ['html', { outputFolder: 'playwright-report', open: 'never' }]],

  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || 'http://127.0.0.1:5173',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    actionTimeout: 10_000,
    navigationTimeout: 30_000,
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],

  webServer: process.env.PLAYWRIGHT_SKIP_WEBSERVER
    ? undefined
    : {
        command: 'npm run client:dev',
        url: 'http://127.0.0.1:5173',
        timeout: 120_000,
        reuseExistingServer: true,
      },
});
