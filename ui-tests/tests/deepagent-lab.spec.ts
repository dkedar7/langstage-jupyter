import { expect, test } from '@jupyterlab/galata';

/**
 * Smoke test: open the Deep Agents chat sidebar, confirm it connects to the
 * (stub) agent, send a message, and verify the streamed reply renders.
 *
 * The backend agent is the model-free stub (see stub_agent.py), so this runs
 * deterministically with no API key.
 */
test('chat sidebar connects to the agent and renders a reply', async ({
  page
}) => {
  // The extension activates on startup and registers an "open chat" command.
  await page.waitForCondition(
    async () =>
      await page.evaluate(() =>
        Boolean(
          (window as any).jupyterapp?.commands?.hasCommand(
            'deepagents:open-chat'
          )
        )
      )
  );
  await page.evaluate(() =>
    (window as any).jupyterapp.commands.execute('deepagents:open-chat')
  );

  const chat = page.locator('.deepagents-chat-container');
  await expect(chat).toBeVisible();

  // Health check fires ~2s after mount; wait for the "healthy" indicator.
  await expect(page.locator('.deepagents-status-healthy')).toBeVisible({
    timeout: 30_000
  });

  // Send a message.
  const input = page.locator('.deepagents-chat-input');
  await input.fill('ping');
  await page.locator('.deepagents-send-button').click();

  // User message echoed into the transcript.
  await expect(
    page.locator('.deepagents-message-user').filter({ hasText: 'ping' })
  ).toBeVisible();

  // Stub agent replies "stub reply: ping".
  await expect(
    page
      .locator('.deepagents-message-assistant')
      .filter({ hasText: 'stub reply: ping' })
  ).toBeVisible({ timeout: 30_000 });
});
