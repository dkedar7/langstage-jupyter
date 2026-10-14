import { expect, test } from '@playwright/test';
import { apiUrl, requestFailedMessage } from '../../src/handler';
import { displayArgs, respondDecisions, summarizeActions } from '../../src/interrupt';
import { SSELineBuffer, TurnAccumulator } from '../../src/stream';

/**
 * Unit tests for the sidebar's plain logic. They import `src/` directly and need
 * no browser; Playwright is just the runner this repo already has.
 */

test.describe('apiUrl (gh #170)', () => {
  test('a root base URL', () => {
    expect(apiUrl('chat', '/')).toBe('/langstage-jupyter/chat');
    expect(apiUrl('resume', 'http://localhost:8888/')).toBe(
      'http://localhost:8888/langstage-jupyter/resume'
    );
  });

  test('a JupyterHub user base URL', () => {
    expect(apiUrl('chat', '/user/alice/')).toBe('/user/alice/langstage-jupyter/chat');
    expect(apiUrl('resume', 'https://hub.example.com/user/alice/')).toBe(
      'https://hub.example.com/user/alice/langstage-jupyter/resume'
    );
  });
});

test.describe('requestFailedMessage (gh #170)', () => {
  test('names the endpoint and status, and ignores an HTML body', () => {
    expect(
      requestFailedMessage('chat', 403, 'Forbidden', '<html><title>403</title></html>')
    ).toBe('Chat request failed: 403 Forbidden');
  });

  test("quotes the server's JSON message, shortened", () => {
    expect(
      requestFailedMessage('resume', 400, 'Bad Request', '{"message": "No thread"}')
    ).toBe('Resume request failed: 400 Bad Request (No thread)');
    const long = requestFailedMessage('chat', 500, '', JSON.stringify({ message: 'x'.repeat(500) }));
    expect(long.startsWith('Chat request failed: 500 (')).toBe(true);
    expect(long.length).toBeLessThan(240);
  });
});

test.describe('SSELineBuffer', () => {
  test('keeps a frame split across reads', () => {
    const lines = new SSELineBuffer();
    expect(lines.push('data: {"a": 1}\n\ndata: {"b"')).toEqual(['{"a": 1}']);
    expect(lines.push(': 2}\n\n')).toEqual(['{"b": 2}']);
    expect(lines.push('data: {"c": 3}')).toEqual([]);
    expect(lines.end()).toEqual(['{"c": 3}']);
  });
});

test.describe('TurnAccumulator (langstage#196)', () => {
  const streaming = (fields: Record<string, unknown>) => ({ status: 'streaming', ...fields });

  test('accumulates reasoning deltas apart from the answer', () => {
    const turn = new TurnAccumulator();
    turn.add(streaming({ reasoning: 'Let me reason. ', node: 'agent' }));
    turn.add(streaming({ reasoning: 'Step two.', node: 'agent' }));
    turn.add(streaming({ chunk: 'Done ', node: 'agent' }));
    turn.add(streaming({ chunk: 'reasoning.', node: 'agent' }));

    const t = turn.snapshot();
    expect(t.reasoning).toBe('Let me reason. Step two.');
    expect(t.content).toBe('Done reasoning.');
    expect(t.intermediates).toEqual(['Done reasoning.']);
  });

  test('reasoning after a tool call starts a new paragraph', () => {
    const turn = new TurnAccumulator();
    turn.add(streaming({ reasoning: 'First I look.', node: 'agent' }));
    turn.add(streaming({ tool_calls: [{ id: 'c1', name: 'search', args: {} }] }));
    turn.add(streaming({ reasoning: 'Now I answer.', node: 'agent' }));

    const t = turn.snapshot();
    expect(t.reasoning).toBe('First I look.\n\nNow I answer.');
    expect(t.toolCalls.map(c => c.name)).toEqual(['search']);
  });

  test('text runs still split at a node or tool break', () => {
    const turn = new TurnAccumulator();
    turn.add(streaming({ chunk: 'Planning.', node: 'planner' }));
    turn.add(streaming({ chunk: 'Look', node: 'agent' }));
    turn.add(streaming({ chunk: 'ing.', node: 'agent' }));
    turn.add(streaming({ tool_calls: [{ id: 'c1', name: 'search', args: {} }] }));
    turn.add(streaming({ chunk: 'Answer.', node: 'agent' }));

    const t = turn.snapshot();
    expect(t.intermediates).toEqual(['Planning.', 'Looking.', 'Answer.']);
    expect(t.content).toBe('Answer.');
    expect(t.reasoning).toBe('');
  });
});

test.describe('TurnAccumulator message_id (langstage-core#200)', () => {
  const streaming = (fields: Record<string, unknown>) => ({ status: 'streaming', ...fields });

  test('a new message_id in the same node starts a new run', () => {
    const turn = new TurnAccumulator();
    turn.add(streaming({ chunk: 'Routing ', node: 'agent', message_id: 'm1' }));
    turn.add(streaming({ chunk: 'to math.', node: 'agent', message_id: 'm1' }));
    turn.add(streaming({ chunk: 'The answer ', node: 'agent', message_id: 'm2' }));
    turn.add(streaming({ chunk: 'is 4.', node: 'agent', message_id: 'm2' }));

    const t = turn.snapshot();
    expect(t.intermediates).toEqual(['Routing to math.', 'The answer is 4.']);
    expect(t.content).toBe('The answer is 4.');
  });

  test('frames without a message_id still join by node', () => {
    const turn = new TurnAccumulator();
    turn.add(streaming({ chunk: 'Hel', node: 'agent' }));
    turn.add(streaming({ chunk: 'lo.', node: 'agent' }));
    expect(turn.snapshot().intermediates).toEqual(['Hello.']);
  });
});

test.describe('summarizeActions (gh #173)', () => {
  test('reads the question out of args', () => {
    const [action] = summarizeActions([
      { action: 'ask_user', args: { question: 'What should I call you?' } }
    ]);
    expect(action.name).toBe('ask_user');
    expect(action.question).toBe('What should I call you?');
    // The question shows as text, so it is not repeated in the args block.
    expect(displayArgs(action)).toBeUndefined();
  });

  test('labels the action from name, action, or tool', () => {
    expect(summarizeActions([{ name: 'write_file' }])[0].name).toBe('write_file');
    expect(summarizeActions([{ action: 'ask_user' }])[0].name).toBe('ask_user');
    expect(summarizeActions([{ tool: 'run_query' }])[0].name).toBe('run_query');
    expect(summarizeActions([{ action_request: { action: 'confirm' } }])[0].name).toBe(
      'confirm'
    );
  });

  test('a plain-string request is shown as its text', () => {
    const [action] = summarizeActions(['Deploy to production?']);
    expect(action.text).toBe('Deploy to production?');
    expect(action.name).toBe('');
  });

  test('other args stay visible', () => {
    const [action] = summarizeActions([
      { name: 'write_file', args: { path: 'a.txt', question: 'Overwrite?' } }
    ]);
    expect(action.question).toBe('Overwrite?');
    expect(displayArgs(action)).toEqual({ path: 'a.txt' });
  });
});

test.describe('respondDecisions (gh #173)', () => {
  test('one respond decision per action request, trimmed', () => {
    expect(respondDecisions([{ action: 'a' }, { action: 'b' }], '  Kedar ')).toEqual([
      { type: 'respond', message: 'Kedar' },
      { type: 'respond', message: 'Kedar' }
    ]);
  });

  test('a single decision when the interrupt lists no requests', () => {
    expect(respondDecisions([], 'yes')).toEqual([{ type: 'respond', message: 'yes' }]);
  });

  test('blank text answers nothing', () => {
    expect(respondDecisions([{ action: 'a' }], '   ')).toEqual([]);
  });
});
