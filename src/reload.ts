import { JupyterFrontEnd } from '@jupyterlab/application';

/**
 * Keep open notebook tabs in step with what the agent writes (gh #160).
 *
 * The notebook tools save through the Jupyter contents API. Without real-time
 * collaboration an open tab doesn't notice, so it kept its pre-agent model and the
 * tab's next save wrote that stale copy back over the agent's cells and outputs.
 * The sidebar now reloads (reverts) the open document once the tool that wrote it
 * has returned.
 */

/** Notebook tools that write the notebook named by their `notebook_path` arg. */
export const NOTEBOOK_WRITE_TOOLS = new Set([
  'create_notebook',
  'insert_code_cell',
  'insert_markdown_cell',
  'modify_cell',
  'delete_cell',
  'execute_cell'
]);

/** A server-root-relative path, as the tools and `context.path` both mean it. */
export function normalizePath(path: string): string {
  return path.trim().replace(/^\/+|\/+$/g, '');
}

/**
 * Remembers which notebook each in-flight writing tool call targets, so its
 * `tool_result` frame can reload that notebook. Lives across the send and resume
 * streams, because a human-in-the-loop interrupt can split a call from its result.
 */
export class AgentWriteTracker {
  private pending = new Map<string, string>();

  /** Record the writing calls in a `tool_calls` frame. */
  noteCalls(calls: Array<{ id?: string; name?: string; args?: any }>): void {
    for (const call of calls) {
      const path = call?.args?.notebook_path;
      if (
        call?.id &&
        call.name &&
        NOTEBOOK_WRITE_TOOLS.has(call.name) &&
        typeof path === 'string' &&
        normalizePath(path)
      ) {
        this.pending.set(call.id, normalizePath(path));
      }
    }
  }

  /** The notebook a finished call wrote, if it was a writing call we saw start. */
  takeResult(id: string | undefined): string | undefined {
    if (!id) {
      return undefined;
    }
    const path = this.pending.get(id);
    this.pending.delete(id);
    return path;
  }

  /** Every notebook still pending (the turn ended without some result frames). */
  takeAll(): string[] {
    const paths = [...new Set(this.pending.values())];
    this.pending.clear();
    return paths;
  }
}

/**
 * Revert every open, unmodified document for `path` from disk. A tab with unsaved
 * user edits is left alone: reverting would discard them, and JupyterLab's own
 * "file changed on disk" prompt still guards its next save. Returns how many tabs
 * were reloaded.
 */
export async function reloadOpenDocument(
  shell: JupyterFrontEnd.IShell | null,
  path: string
): Promise<number> {
  if (!shell) {
    return 0;
  }
  const target = normalizePath(path);
  const reverts: Promise<void>[] = [];
  const seen = new Set<any>();
  for (const widget of shell.widgets('main')) {
    const context = (widget as any).context;
    if (!context || seen.has(context) || typeof context.revert !== 'function') {
      continue;
    }
    seen.add(context); // several views can share one context
    if (normalizePath(String(context.path ?? '')) !== target) {
      continue;
    }
    if (context.model?.dirty) {
      continue;
    }
    reverts.push(context.revert());
  }
  const results = await Promise.allSettled(reverts);
  results.forEach(r => {
    if (r.status === 'rejected') {
      console.warn(`langstage-jupyter: could not reload ${target}:`, r.reason);
    }
  });
  return reverts.length;
}
