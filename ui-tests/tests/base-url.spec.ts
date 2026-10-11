import { expect, Page, test } from '@playwright/test';

/**
 * gh #170: the sidebar under a non-root base_url.
 *
 * Every JupyterHub server is served at `/user/<name>/`. The sidebar POSTed chat and
 * resume to hard-coded root paths (`/langstage-jupyter/chat`), outside the server's
 * prefix, so they got a 403. The turn then ended with no reply and no error.
 *
 * These tests drive the second server from playwright.config.js, at `/user/alice/`
 * with XSRF checks on, with plain Playwright (Galata's fixtures assume a root URL).
 * That server's `base-url-settings/overrides.json` turns off JupyterLab's news
 * prompt, as Galata's fixtures do; on a fresh profile its toast covers the sidebar's
 * send button.
 */
const BASE = 'http://localhost:8889/user/alice/';
const PREFIX = '/user/alice/langstage-jupyter/';

async function openHealthyChat(page: Page) {
  await page.goto(`${BASE}lab`);
  await page.waitForFunction(
    () =>
      Boolean(
        (window as any).jupyterapp?.commands?.hasCommand('deepagents:open-chat')
      ),
    null,
    { timeout: 60_000 }
  );
  await page.evaluate(() =>
    (window as any).jupyterapp.commands.execute('deepagents:open-chat')
  );
  await expect(page.locator('.deepagents-status-healthy')).toBeVisible({
    timeout: 30_000
  });
}

async function send(page: Page, text: string) {
  await page.locator('.deepagents-chat-input').fill(text);
  await page.locator('.deepagents-send-button').click();
}

test('chat posts under the base_url and the reply arrives', async ({ page }) => {
  await openHealthyChat(page);

  const chat = page.waitForResponse(r =>
    r.url().includes('langstage-jupyter/chat')
  );
  await send(page, 'ping');
  const response = await chat;

  expect(new URL(response.url()).pathname).toBe(`${PREFIX}chat`);
  expect(response.status()).toBe(200);
  await expect(
    page
      .locator('.deepagents-message-assistant')
      .filter({ hasText: 'stub reply: ping' })
  ).toBeVisible({ timeout: 30_000 });
});

test('resume posts under the base_url', async ({ page }) => {
  // The stub agent never interrupts, so the chat stream is stubbed with an interrupt.
  await page.route(/\/langstage-jupyter\/chat(\?|$)/, route =>
    route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
      body: [
        {
          status: 'interrupt',
          interrupt: {
            action_requests: [{ action: 'ask_user', args: {} }],
            allowed_decisions: ['respond', 'approve']
          }
        },
        { status: 'complete', outcome: 'interrupted' }
      ]
        .map(f => `data: ${JSON.stringify(f)}\n\n`)
        .join('')
    })
  );
  const resumeUrls: string[] = [];
  await page.route(/\/langstage-jupyter\/resume(\?|$)/, route => {
    resumeUrls.push(route.request().url());
    return route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
      body: [
        { status: 'streaming', chunk: 'Resumed.', node: 'model' },
        { status: 'complete', outcome: 'complete' }
      ]
        .map(f => `data: ${JSON.stringify(f)}\n\n`)
        .join('')
    });
  });

  await openHealthyChat(page);
  await send(page, 'ask me');
  await page.locator('.deepagents-approve-btn').click();

  await expect(
    page.locator('.deepagents-message-assistant').filter({ hasText: 'Resumed.' })
  ).toBeVisible({ timeout: 15_000 });
  expect(resumeUrls.map(u => new URL(u).pathname)).toEqual([`${PREFIX}resume`]);
});
