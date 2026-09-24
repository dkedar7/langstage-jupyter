import { expect, IJupyterLabPageFixture, test } from '@jupyterlab/galata';

/**
 * Smoke test: open the LangStage chat sidebar, confirm it connects to the
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

// ── gh #160: an open notebook tab follows what the agent writes ─────────────────
//
// When an agent tool writes a notebook that is open in a tab, the tab must reload
// it. Before, the tab kept its pre-agent model and its next save wrote that stale
// copy back over the agent's cells and outputs.
//
// The /chat stream is stubbed with the frames a real `insert_code_cell` turn emits
// (tool_calls, then tool_result, then complete). The "agent's write" is a
// contents-API save, which is exactly what the notebook tools do.

const AGENT_TEXT = 'written by the agent';

async function saveNotebook(page: IJupyterLabPageFixture, path: string, cells: any[]) {
  await page.evaluate(
    async ([notebookPath, notebookCells]) => {
      await (window as any).jupyterapp.serviceManager.contents.save(notebookPath, {
        type: 'notebook',
        format: 'json',
        content: {
          cells: notebookCells,
          // A kernelspec, so opening the notebook doesn't raise "Select Kernel".
          metadata: {
            kernelspec: {
              name: 'python3',
              display_name: 'Python 3',
              language: 'python'
            }
          },
          nbformat: 4,
          nbformat_minor: 4
        }
      });
    },
    [path, cells] as [string, any[]]
  );
}

const currentModelDirty = (page: IJupyterLabPageFixture) =>
  page.evaluate(() =>
    Boolean((window as any).jupyterapp.shell.currentWidget?.context?.model?.dirty)
  );

/** Open `path` like a user would, and save it once its kernel is up. */
async function openSavedNotebook(page: IJupyterLabPageFixture, path: string) {
  await saveNotebook(page, path, []);
  await page.evaluate(
    notebookPath =>
      (window as any).jupyterapp.commands.execute('docmanager:open', {
        path: notebookPath
      }),
    path
  );
  await expect(page.locator('.jp-NotebookPanel')).toBeVisible();
  // Starting the kernel rewrites kernelspec/language_info metadata, which marks the
  // tab dirty. A user's notebook has been saved since, so save once it is idle.
  await page.waitForCondition(() =>
    page.evaluate(
      () =>
        (window as any).jupyterapp.shell.currentWidget?.sessionContext?.session
          ?.kernel?.status === 'idle'
    )
  );
  await page.evaluate(() =>
    (window as any).jupyterapp.commands.execute('docmanager:save')
  );
  await page.waitForCondition(async () => !(await currentModelDirty(page)));
}

/** The agent writes `path`, then the sidebar receives that tool call's frames. */
async function runAgentWrite(page: IJupyterLabPageFixture, path: string) {
  await saveNotebook(page, path, [
    {
      cell_type: 'code',
      source: `print('${AGENT_TEXT}')`,
      metadata: {},
      execution_count: 1,
      outputs: [{ output_type: 'stream', name: 'stdout', text: `${AGENT_TEXT}\n` }]
    }
  ]);

  const frames = [
    {
      status: 'streaming',
      tool_calls: [
        {
          id: 'call-1',
          name: 'insert_code_cell',
          args: { code: `print('${AGENT_TEXT}')`, notebook_path: path }
        }
      ]
    },
    {
      status: 'streaming',
      tool_result: `Inserted code cell at index 0 in ${path}`,
      id: 'call-1',
      name: 'insert_code_cell',
      tool_status: 'success'
    },
    { status: 'streaming', chunk: 'Done.', node: 'model', message_id: 'm1' },
    { status: 'complete', outcome: 'complete' }
  ];
  await page.route('**/langstage-jupyter/chat', route =>
    route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
      body: frames.map(f => `data: ${JSON.stringify(f)}\n\n`).join('')
    })
  );

  await page.evaluate(() =>
    (window as any).jupyterapp.commands.execute('deepagents:open-chat')
  );
  await expect(page.locator('.deepagents-status-healthy')).toBeVisible({
    timeout: 30_000
  });
  await page.locator('.deepagents-chat-input').fill('add a cell');
  await page.locator('.deepagents-send-button').click();
  await expect(
    page.locator('.deepagents-message-assistant').filter({ hasText: 'Done.' })
  ).toBeVisible({ timeout: 15_000 });
}

const agentCell = (page: IJupyterLabPageFixture) =>
  page.locator('.jp-NotebookPanel .jp-Cell').filter({ hasText: AGENT_TEXT });

test('an open notebook reloads after an agent tool writes it', async ({
  page
}) => {
  // At the server root: the path the notebook tools pass is root-relative too.
  const path = `agent-write-${Date.now()}.ipynb`;
  await openSavedNotebook(page, path);

  await runAgentWrite(page, path);

  await expect(agentCell(page)).toHaveCount(1, { timeout: 15_000 });
  await expect(
    page.locator('.jp-NotebookPanel .jp-OutputArea-output', { hasText: AGENT_TEXT })
  ).toBeVisible();
  expect(await currentModelDirty(page)).toBe(false);
});

test('a tab with unsaved changes is not reloaded, and the user is told', async ({
  page
}) => {
  const path = `agent-write-dirty-${Date.now()}.ipynb`;
  await openSavedNotebook(page, path);
  // The user has an unsaved edit in the tab.
  await page.evaluate(() =>
    (window as any).jupyterapp.commands.execute('notebook:insert-cell-below')
  );
  await page.waitForCondition(() => currentModelDirty(page));

  await runAgentWrite(page, path);

  await expect(
    page.locator('.deepagents-message-system').filter({ hasText: 'unsaved changes' })
  ).toBeVisible({ timeout: 15_000 });
  await expect(agentCell(page)).toHaveCount(0);
  expect(await currentModelDirty(page)).toBe(true);
});
