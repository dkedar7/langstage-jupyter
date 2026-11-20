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

/** Whether the open tab for `path` has unsaved changes (null if none is open). */
const tabDirty = (page: IJupyterLabPageFixture, path: string) =>
  page.evaluate(notebookPath => {
    for (const widget of Array.from((window as any).jupyterapp.shell.widgets('main'))) {
      const context = (widget as any).context;
      if (context?.path === notebookPath) {
        return Boolean(context.model?.dirty);
      }
    }
    return null;
  }, path);

/** Open `path` like a user would, and save it once its kernel is up. */
async function openSavedNotebook(
  page: IJupyterLabPageFixture,
  path: string,
  cells: any[] = []
) {
  await saveNotebook(page, path, cells);
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

/**
 * The sidebar sends a turn; during it the agent writes `path` and the sidebar
 * receives that tool call's frames. The write happens once the /chat request
 * arrives, as a real tool's does, so it comes after the sidebar's own save of the
 * open notebooks (gh #207). `duringTurn` runs then too, before the write: what the
 * user does while the agent works.
 */
async function runAgentWrite(
  page: IJupyterLabPageFixture,
  path: string,
  duringTurn?: () => Promise<void>
) {
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
  await page.route(CHAT_URL, async route => {
    if (duringTurn) {
      await duringTurn();
    }
    await saveNotebook(page, path, [
      {
        cell_type: 'code',
        source: `print('${AGENT_TEXT}')`,
        metadata: {},
        execution_count: 1,
        outputs: [{ output_type: 'stream', name: 'stdout', text: `${AGENT_TEXT}\n` }]
      }
    ]);
    await route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
      body: frames.map(f => `data: ${JSON.stringify(f)}\n\n`).join('')
    });
  });
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
  // Open tabs are saved before the turn (gh #207), so the tab can only have unsaved
  // changes the agent's write would clobber if the user edits it during the turn.
  await runAgentWrite(page, path, async () => {
    await page.evaluate(() =>
      (window as any).jupyterapp.commands.execute('notebook:insert-cell-below')
    );
    await page.waitForCondition(() => currentModelDirty(page));
  });

  await expect(
    page.locator('.deepagents-message-system').filter({ hasText: 'unsaved changes' })
  ).toBeVisible({ timeout: 15_000 });
  expect(await tabDirty(page, path)).toBe(true);
});

// ── gh #207: open notebooks are saved before a turn ──────────────────────────────
//
// The notebook tools read a notebook through the contents API, so they see it as
// last saved. A run the user had just done sat in the dirty tab until autosave (two
// minutes), so `read_cell_outputs` answered "no saved outputs" to "why did cell 0 fail?".

test('the focused notebook is saved before a turn, with the error it just raised', async ({
  page
}) => {
  const path = `save-before-turn-${Date.now()}.ipynb`;
  await openSavedNotebook(page, path, [
    {
      cell_type: 'code',
      source: "sales = {'Jul': 120}\nsales['revenue']",
      metadata: {},
      execution_count: null,
      outputs: []
    }
  ]);
  await page.evaluate(() =>
    (window as any).jupyterapp.commands.execute('notebook:run-cell')
  );
  await expect(
    page.locator('.jp-NotebookPanel .jp-OutputArea-output', { hasText: 'KeyError' })
  ).toBeVisible({ timeout: 30_000 });
  await page.waitForCondition(() => currentModelDirty(page));

  await stubStream(page, CHAT_URL, [
    { status: 'streaming', chunk: 'Looking.', node: 'model', message_id: 'm1' },
    { status: 'complete', outcome: 'complete' }
  ]);
  await sendInHealthyChat(page, 'why did cell 0 fail?');
  await expect(
    page.locator('.deepagents-message-assistant').filter({ hasText: 'Looking.' })
  ).toBeVisible({ timeout: 15_000 });

  // What the notebook tools read: the saved copy now holds the traceback.
  const saved = await page.evaluate(
    async notebookPath =>
      (await (window as any).jupyterapp.serviceManager.contents.get(notebookPath))
        .content,
    path
  );
  const errors = saved.cells[0].outputs.filter((o: any) => o.output_type === 'error');
  expect(errors.map((o: any) => o.ename)).toEqual(['KeyError']);
  expect(await currentModelDirty(page)).toBe(false);
});

test('a focused tab with unsaved changes is saved first, so the agent write reloads it', async ({
  page
}) => {
  const path = `agent-write-focused-dirty-${Date.now()}.ipynb`;
  await openSavedNotebook(page, path);
  await page.evaluate(() =>
    (window as any).jupyterapp.commands.execute('notebook:insert-cell-below')
  );
  await page.waitForCondition(() => currentModelDirty(page));

  await runAgentWrite(page, path);

  await expect(agentCell(page)).toHaveCount(1, { timeout: 15_000 });
  await expect(
    page.locator('.deepagents-message-system').filter({ hasText: 'unsaved changes' })
  ).toHaveCount(0);
});

test('a notebook in another tab is saved too, with the error it just raised', async ({
  page
}) => {
  const stamp = Date.now();
  const failing = `save-unfocused-${stamp}.ipynb`;
  const other = `save-unfocused-other-${stamp}.ipynb`;
  await openSavedNotebook(page, failing, [
    {
      cell_type: 'code',
      source: "sales = {'Jul': 120}
sales['revenue']",
      metadata: {},
      execution_count: null,
      outputs: []
    }
  ]);
  await page.evaluate(() =>
    (window as any).jupyterapp.commands.execute('notebook:run-cell')
  );
  await expect(
    page.locator('.jp-NotebookPanel .jp-OutputArea-output', { hasText: 'KeyError' })
  ).toBeVisible({ timeout: 30_000 });
  await page.waitForCondition(() => currentModelDirty(page));

  // The user moves on to another notebook before asking. (Opened by hand:
  // openSavedNotebook expects a single notebook panel.)
  await saveNotebook(page, other, []);
  await page.evaluate(
    notebookPath =>
      (window as any).jupyterapp.commands.execute('docmanager:open', {
        path: notebookPath
      }),
    other
  );
  await page.waitForCondition(() =>
    page.evaluate(
      notebookPath =>
        (window as any).jupyterapp.shell.currentWidget?.context?.path === notebookPath,
      other
    )
  );
  expect(await tabDirty(page, failing)).toBe(true);

  await stubStream(page, CHAT_URL, [
    { status: 'streaming', chunk: 'Looking.', node: 'model', message_id: 'm1' },
    { status: 'complete', outcome: 'complete' }
  ]);
  await sendInHealthyChat(page, 'why did the sales cell fail?');
  await expect(
    page.locator('.deepagents-message-assistant').filter({ hasText: 'Looking.' })
  ).toBeVisible({ timeout: 15_000 });

  // What the notebook tools read: the saved copy of the unfocused tab holds the traceback.
  const saved = await page.evaluate(
    async notebookPath =>
      (await (window as any).jupyterapp.serviceManager.contents.get(notebookPath))
        .content,
    failing
  );
  const errors = saved.cells[0].outputs.filter((o: any) => o.output_type === 'error');
  expect(errors.map((o: any) => o.ename)).toEqual(['KeyError']);
  expect(await tabDirty(page, failing)).toBe(false);
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

// ── gh #173: the card shows the question and can answer it ──────────────────────────
//
// The card offered only approve/reject/edit and never showed the question, so an agent
// asking the user something (allowed_decisions includes `respond`) couldn't be answered.

const askInterrupt = (allowed: string[], request: unknown = {
  action: 'ask_user',
  args: { question: 'What should I call you?' }
}) => [
  {
    status: 'interrupt',
    interrupt: { action_requests: [request], review_configs: [], allowed_decisions: allowed }
  },
  { status: 'complete', outcome: 'interrupted' }
];

test('the card shows the question and sends a respond decision', async ({ page }) => {
  await stubStream(page, CHAT_URL, askInterrupt(['respond', 'approve']));
  const resumeBodies: any[] = [];
  await page.route(RESUME_URL, route => {
    resumeBodies.push(route.request().postDataJSON());
    return route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
      body: [
        { status: 'streaming', chunk: 'Nice to meet you, Kedar.', node: 'agent', message_id: 'm2' },
        { status: 'complete', outcome: 'complete' }
      ]
        .map(f => `data: ${JSON.stringify(f)}\n\n`)
        .join('')
    });
  });
  await sendInHealthyChat(page, 'ask me');

  const card = page.locator('.deepagents-interrupt');
  await expect(card).toBeVisible({ timeout: 15_000 });
  await expect(card.locator('.deepagents-interrupt-question')).toHaveText(
    'What should I call you?'
  );
  const respond = card.locator('.deepagents-respond-btn');
  await expect(respond).toBeDisabled();
  const field = card.locator('.deepagents-interrupt-response');
  await field.fill('   ');
  await expect(respond).toBeDisabled();
  await field.fill('Kedar');
  await expect(respond).toBeEnabled();
  await respond.click();

  await expect(
    page.locator('.deepagents-message-assistant').filter({ hasText: 'Nice to meet you, Kedar.' })
  ).toBeVisible({ timeout: 15_000 });
  expect(resumeBodies).toHaveLength(1);
  expect(resumeBodies[0].decisions).toEqual([{ type: 'respond', message: 'Kedar' }]);
  // Answered: the card's controls are off.
  await expect(field).toBeDisabled();
  await expect(card.locator('.deepagents-approve-btn')).toBeDisabled();
});

test('no respond control when the interrupt does not allow it', async ({ page }) => {
  await stubStream(
    page,
    CHAT_URL,
    askInterrupt(['approve', 'reject'], {
      name: 'write_file',
      args: { path: 'a.txt' },
      description: 'Write a.txt'
    })
  );
  await sendInHealthyChat(page, 'write it');

  const card = page.locator('.deepagents-interrupt');
  await expect(card).toBeVisible({ timeout: 15_000 });
  await expect(card.locator('.deepagents-interrupt-description strong')).toHaveText('write_file');
  await expect(card.locator('.deepagents-interrupt-args')).toContainText('a.txt');
  await expect(card.locator('.deepagents-approve-btn')).toBeVisible();
  await expect(card.locator('.deepagents-reject-btn')).toBeVisible();
  await expect(card.locator('.deepagents-interrupt-response')).toHaveCount(0);
  await expect(card.locator('.deepagents-respond-btn')).toHaveCount(0);
});

test('a plain-string request shows as its text', async ({ page }) => {
  await stubStream(page, CHAT_URL, askInterrupt(['respond'], 'Which branch should I use?'));
  await sendInHealthyChat(page, 'ask me');

  const card = page.locator('.deepagents-interrupt');
  await expect(card).toBeVisible({ timeout: 15_000 });
  await expect(card.locator('.deepagents-interrupt-question')).toHaveText(
    'Which branch should I use?'
  );
  await expect(card.locator('.deepagents-interrupt-description strong')).toHaveCount(0);
  await expect(card.locator('.deepagents-respond-btn')).toBeVisible();
});

test('an answered card stays off when a later interrupt arrives', async ({ page }) => {
  await stubStream(page, CHAT_URL, askInterrupt(['approve']));
  await stubStream(
    page,
    RESUME_URL,
    askInterrupt(['approve'], { action: 'confirm', args: { question: 'Proceed?' } })
  );
  await sendInHealthyChat(page, 'ask me');

  const cards = page.locator('.deepagents-interrupt');
  await expect(cards).toHaveCount(1, { timeout: 15_000 });
  await cards.first().locator('.deepagents-approve-btn').click();
  await expect(cards).toHaveCount(2, { timeout: 15_000 });
  await expect(cards.nth(1).locator('.deepagents-approve-btn')).toBeEnabled();
  await expect(cards.first().locator('.deepagents-approve-btn')).toBeDisabled();
});
