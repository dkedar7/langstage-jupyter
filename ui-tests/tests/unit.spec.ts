import { expect, test } from '@playwright/test';
import { apiUrl, requestFailedMessage } from '../../src/handler';
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
