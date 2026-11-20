/**
 * Save the open notebooks with unsaved changes before the agent's turn (gh #207).
 *
 * The notebook tools read a notebook through the Jupyter contents API, so they see it
 * as last saved. JupyterLab autosaves every two minutes, and "why did cell 0 fail?"
 * usually comes seconds after the run. Saving open notebooks first lets
 * `read_cell_outputs` and `get_notebook_state` see the run the user is asking about,
 * in whichever tab it happened. It also leaves those tabs clean, so an agent write in
 * the same turn can reload them (gh #160) instead of being skipped as "unsaved changes".
 *
 * Only notebooks are saved: the notebook tools read nothing else, and saving a text
 * file the user is half-way through editing would surprise them for no gain.
 */

/** How long a turn waits for the saves before it is sent anyway. */
export const SAVE_BEFORE_TURN_TIMEOUT_MS = 5000;

/** The parts of a JupyterLab document context this step uses. */
export interface ISaveableContext {
  path: string;
  isReady?: boolean;
  isDisposed?: boolean;
  model?: { dirty?: boolean; readOnly?: boolean } | null;
  contentsModel?: { writable?: boolean } | null;
  save(): Promise<void>;
}

/** What {@link saveBeforeTurn} did. */
export type SaveOutcome =
  | { kind: 'skipped' } // nothing to save: no notebook, already clean, or read-only
  | { kind: 'saved'; path: string }
  | { kind: 'failed'; path: string; reason: string }
  | { kind: 'timeout'; path: string };

/** Whether `context` is an open, writable document with unsaved changes. */
export function needsSave(context: ISaveableContext | null | undefined): boolean {
  if (!context || context.isDisposed || context.isReady === false) {
    return false;
  }
  if (!context.model?.dirty) {
    return false;
  }
  // A read-only notebook, or one the server says it can't write, would only fail.
  if (context.model.readOnly || context.contentsModel?.writable === false) {
    return false;
  }
  return true;
}

/**
 * Save `context` if it has unsaved changes. Never throws, and never waits longer than
 * `timeoutMs`: a failed or slow save must not stop the turn. A save that times out
 * keeps running (JupyterLab may be showing its "file changed on disk" dialog).
 */
export async function saveBeforeTurn(
  context: ISaveableContext | null | undefined,
  timeoutMs: number = SAVE_BEFORE_TURN_TIMEOUT_MS
): Promise<SaveOutcome> {
  if (!context || !needsSave(context)) {
    return { kind: 'skipped' };
  }
  const path = context.path;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<'timeout'>(resolve => {
    timer = setTimeout(() => resolve('timeout'), timeoutMs);
  });
  try {
    const result = await Promise.race([
      Promise.resolve()
        .then(() => context.save())
        .then(() => 'saved' as const),
      timeout
    ]);
    if (result === 'timeout') {
      return { kind: 'timeout', path };
    }
    return { kind: 'saved', path };
  } catch (error) {
    const reason =
      error instanceof Error ? error.message : String(error ?? 'unknown error');
    return { kind: 'failed', path, reason: reason || 'unknown error' };
  } finally {
    if (timer !== undefined) {
      clearTimeout(timer);
    }
  }
}

/** The sidebar note for an outcome the user should hear about, or null. */
export function saveOutcomeNote(outcome: SaveOutcome): string | null {
  switch (outcome.kind) {
    case 'failed':
      return (
        `Could not save ${outcome.path} before this turn (${outcome.reason}). ` +
        'The agent sees the copy last saved.'
      );
    case 'timeout':
      return (
        `${outcome.path} is still saving, so the agent may see the copy last saved.`
      );
    default:
      return null;
  }
}

/**
 * Save every notebook in `contexts` that has unsaved changes, all at once. Each save is
 * capped at `timeoutMs` and the saves run in parallel, so the whole step waits at most
 * about `timeoutMs` however many tabs are open. A context shared by several views is
 * saved once. Never throws.
 */
export async function saveOpenNotebooks(
  contexts: Iterable<ISaveableContext | null | undefined>,
  timeoutMs: number = SAVE_BEFORE_TURN_TIMEOUT_MS
): Promise<SaveOutcome[]> {
  const seen = new Set<ISaveableContext>();
  const targets: ISaveableContext[] = [];
  for (const context of contexts) {
    if (!context || seen.has(context)) {
      continue;
    }
    seen.add(context);
    if (needsSave(context)) {
      targets.push(context);
    }
  }
  return Promise.all(targets.map(context => saveBeforeTurn(context, timeoutMs)));
}

/**
 * One sidebar note for every save the user should hear about, or null. A single
 * problem reads as {@link saveOutcomeNote} does; several are listed in one note.
 */
export function saveOutcomesNote(outcomes: SaveOutcome[]): string | null {
  const failed = outcomes.filter(
    (o): o is Extract<SaveOutcome, { kind: 'failed' }> => o.kind === 'failed'
  );
  const slow = outcomes.filter(
    (o): o is Extract<SaveOutcome, { kind: 'timeout' }> => o.kind === 'timeout'
  );
  if (failed.length + slow.length === 0) {
    return null;
  }
  if (failed.length + slow.length === 1) {
    return saveOutcomeNote(failed[0] ?? slow[0]);
  }
  const parts: string[] = [];
  if (failed.length > 0) {
    parts.push(
      'Could not save ' +
        failed.map(o => `${o.path} (${o.reason})`).join(', ') +
        ' before this turn.'
    );
  }
  if (slow.length > 0) {
    parts.push(
      `${slow.map(o => o.path).join(', ')} ${slow.length === 1 ? 'is' : 'are'} still saving.`
    );
  }
  parts.push('The agent sees the copies last saved.');
  return parts.join(' ');
}
