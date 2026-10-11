import { expect, IJupyterLabPageFixture, test } from '@jupyterlab/galata';

// The sidebar's /chat and /resume requests. They go through ServerConnection, which
// adds a cache-busting query (`?<time>`), so match with or without one.
const CHAT_URL = /\/langstage-jupyter\/chat(\?|$)/;
const RESUME_URL = /\/langstage-jupyter\/resume(\?|$)/;

/** Serve `frames` as the SSE body of the requests matching `url`. */
async function stubStream(page: IJupyterLabPageFixture, url: RegExp, frames: any[]) {
  await page.route(url, route =>
    route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
      body: frames.map(f => `data: ${JSON.stringify(f)}\n\n`).join('')
    })
  );
}

/** Open the sidebar, wait for the agent, and send `text`. */
async function sendInHealthyChat(page: IJupyterLabPageFixture, text: string) {
  await page.evaluate(() =>
    (window as any).jupyterapp.commands.execute('deepagents:open-chat')
  );
  await expect(page.locator('.deepagents-status-healthy')).toBeVisible({
    timeout: 30_000
  });
  await page.locator('.deepagents-chat-input').fill(text);
  await page.locator('.deepagents-send-button').click();
}

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
  await stubStream(page, CHAT_URL, frames);
  await sendInHealthyChat(page, 'add a cell');
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

// ── gh #168: the approval card names the action for every interrupt shape ────────
//
// The card read `action_requests[0].tool`, which no langstage-core shape sets, so it
// showed an empty "Tool:" (e.g. the demo-tools agent's `ask_user`). The /chat stream
// is stubbed with the interrupt frame core emits for each request shape.

const INTERRUPT_SHAPES: Array<[string, Record<string, unknown>, string]> = [
  [
    'HumanInterrupt list (demo tools ask_user)',
    { action: 'ask_user', args: { question: 'What should I call you?' } },
    'ask_user'
  ],
  [
    'HITL middleware',
    { name: 'write_file', args: { path: 'a.txt' }, description: 'Write a.txt' },
    'write_file'
  ],
  [
    'nested single interrupt',
    { action_request: { action: 'confirm', args: {} }, description: 'Proceed?' },
    'confirm'
  ]
];

for (const [label, request, expected] of INTERRUPT_SHAPES) {
  test(`approval card names the action: ${label}`, async ({ page }) => {
    const frames = [
      {
        status: 'interrupt',
        interrupt: {
          action_requests: [request],
          review_configs: [],
          allowed_decisions: ['respond', 'approve']
        }
      },
      { status: 'complete', outcome: 'interrupted' }
    ];
    await stubStream(page, CHAT_URL, frames);
    await sendInHealthyChat(page, 'ask me');

    const card = page.locator('.deepagents-interrupt');
    await expect(card).toBeVisible({ timeout: 15_000 });
    await expect(card.locator('.deepagents-interrupt-description strong')).toHaveText(
      expected
    );
    await expect(card.locator('.deepagents-approve-btn')).toBeVisible();
  });
}

// ── langstage#196: the sidebar shows the model's reasoning ──────────────────────────
//
// The SSE loop handled only tool_calls, todo_list and chunk, so `reasoning` frames were
// dropped. They now accumulate into a collapsed "Thinking" block above the answer.

test('reasoning streams into a collapsed Thinking block above the answer', async ({
  page
}) => {
  await stubStream(page, CHAT_URL, [
    { status: 'streaming', reasoning: 'Let me reason about this step by step. ', node: 'agent' },
    { status: 'streaming', reasoning: 'The demo streams this as a reasoning frame.', node: 'agent' },
    { status: 'streaming', chunk: 'Done reasoning.', node: 'agent', message_id: 'm1' },
    { status: 'complete', outcome: 'complete' }
  ]);
  await sendInHealthyChat(page, 'think about it');

  const message = page
    .locator('.deepagents-message-assistant')
    .filter({ hasText: 'Done reasoning.' });
  await expect(message).toBeVisible({ timeout: 15_000 });
  const thinking = message.locator('details.deepagents-reasoning');
  await expect(thinking).toHaveCount(1);
  await expect(thinking.locator('summary')).toHaveText('Thinking');
  // Collapsed, and above the answer.
  expect(await thinking.evaluate(d => (d as HTMLDetailsElement).open)).toBe(false);
  await expect(thinking.locator('.deepagents-reasoning-content')).toBeHidden();
  expect(
    await message.evaluate(el => {
      const block = el.querySelector('.deepagents-reasoning')!;
      const answer = el.querySelector('.deepagents-message-content > p')!;
      return Boolean(
        block.compareDocumentPosition(answer) & Node.DOCUMENT_POSITION_FOLLOWING
      );
    })
  ).toBe(true);

  await thinking.locator('summary').click();
  await expect(thinking.locator('.deepagents-reasoning-content')).toHaveText(
    'Let me reason about this step by step. The demo streams this as a reasoning frame.'
  );
});

// ── gh #170: a failed request shows an error, not an empty turn ─────────────────────

test('a failed chat request shows its status', async ({ page }) => {
  await page.route(CHAT_URL, route =>
    route.fulfill({
      status: 403,
      headers: { 'Content-Type': 'text/html' },
      body: '<html><title>403: Forbidden</title></html>'
    })
  );
  await sendInHealthyChat(page, 'hello');

  await expect(
    page
      .locator('.deepagents-message-error')
      .filter({ hasText: 'Chat request failed: 403' })
  ).toBeVisible({ timeout: 15_000 });
});

test('a failed resume request shows its status', async ({ page }) => {
  await stubStream(page, CHAT_URL, [
    {
      status: 'interrupt',
      interrupt: {
        action_requests: [{ action: 'ask_user', args: {} }],
        allowed_decisions: ['respond', 'approve']
      }
    },
    { status: 'complete', outcome: 'interrupted' }
  ]);
  await page.route(RESUME_URL, route =>
    route.fulfill({
      status: 500,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: 'agent crashed' })
    })
  );
  await sendInHealthyChat(page, 'ask me');
  await page.locator('.deepagents-approve-btn').click();

  await expect(
    page
      .locator('.deepagents-message-error')
      .filter({ hasText: 'Resume request failed: 500' })
      .filter({ hasText: 'agent crashed' })
  ).toBeVisible({ timeout: 15_000 });
});
