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

/**
 * A server-root-relative path, as the tools and `context.path` both mean it.
 *
 * The notebook tools accept several spellings of one notebook (gh #187), so this
 * folds them the way the backend's `_norm` does: surrounding whitespace, `\` as a
 * separator, repeated `/`, `.` segments, and leading or trailing `/` all go. `..` is
 * kept as-is: the tools refuse a path with one, so nothing was written (gh #211).
 */
export function normalizePath(path: string): string {
  const parts = path
    .trim()
    .replace(/\\/g, '/')
    .split('/')
    .filter(part => part !== '' && part !== '.');
  return parts.join('/');
}

/** Whether `path` names a host file: `/x`, `C:\x` / `C:/x`, or a UNC `\\host\x`. */
function isHostPath(path: string): boolean {
  const p = path.trim();
  return p.startsWith('/') || p.startsWith('\\') || /^[A-Za-z]:[\\/]/.test(p);
}

/** Whether the serving root is a Windows path, where file names ignore case. */
export function isWindowsRoot(servingRoot: string | null | undefined): boolean {
  return Boolean(
    servingRoot && (/^[A-Za-z]:[\\/]/.test(servingRoot) || servingRoot.startsWith('\\\\'))
  );
}

/**
 * Every root-relative path a tool's `notebook_path` can mean (gh #211).
 *
 * The root-relative reading always counts (`./a.ipynb` and `/a.ipynb` are `a.ipynb`).
 * An absolute host path inside the serving root also counts as its root-relative
 * form, because the tools map it there (gh #203). Reloading a clean tab is harmless,
 * so when both readings name a file, both are reloaded.
 */
export function candidatePaths(
  path: string,
  servingRoot?: string | null
): string[] {
  const out: string[] = [];
  const relative = normalizePath(path);
  if (relative) {
    out.push(relative);
  }
  if (servingRoot && isHostPath(path)) {
    const windows = isWindowsRoot(servingRoot);
    const fold = (s: string) => (windows ? s.toLowerCase() : s);
    // A leading `/` is the only thing that keeps a POSIX path absolute, so add it
    // back after normalizing; a Windows path keeps its drive letter or UNC host.
    const lead = (s: string) => (s.trim().replace(/\\/g, '/').startsWith('/') ? '/' : '');
    const root = lead(servingRoot) + normalizePath(servingRoot);
    const host = lead(path) + normalizePath(path);
    const prefix = root.endsWith('/') ? root : root + '/';
    if (fold(host).startsWith(fold(prefix))) {
      const inside = normalizePath(host.slice(prefix.length));
      if (inside && !out.includes(inside)) {
        out.push(inside);
      }
    }
  }
  return out;
}

/** Whether two root-relative paths name the same file on this server. */
export function samePath(a: string, b: string, servingRoot?: string | null): boolean {
  const x = normalizePath(a);
  const y = normalizePath(b);
  return isWindowsRoot(servingRoot) ? x.toLowerCase() === y.toLowerCase() : x === y;
}

/**
 * Remembers which notebook each in-flight writing tool call targets, so its
 * `tool_result` frame can reload that notebook. Lives across the send and resume
 * streams, because a human-in-the-loop interrupt can split a call from its result.
 */
export class AgentWriteTracker {
  private pending = new Map<string, string>();

  /**
   * The directory JupyterLab serves (from `/health`), used to place an absolute
   * `notebook_path` (gh #211). Unknown until the health check answers.
   */
  servingRoot: string | null = null;

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
        // The raw spelling: it is resolved when the result arrives, so a serving
        // root learned in between still counts.
        this.pending.set(call.id, path);
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

/** What {@link reloadOpenDocument} did with the open tabs for a path. */
export interface IReloadOutcome {
  /** Tabs reverted to the agent's saved version. */
  reloaded: number;
  /** Tabs left alone because they hold unsaved changes. */
  skippedDirty: number;
}

/**
 * Revert every open, unmodified document for `path` from disk. A tab with unsaved
 * changes is left alone, because reverting would discard them. The caller tells
 * the user, and JupyterLab's own "file changed on disk" prompt still guards that
 * tab's next save.
 */
export async function reloadOpenDocument(
  shell: JupyterFrontEnd.IShell | null,
  path: string,
  servingRoot?: string | null
): Promise<IReloadOutcome> {
  const outcome: IReloadOutcome = { reloaded: 0, skippedDirty: 0 };
  if (!shell) {
    return outcome;
  }
  // Every spelling the tools accept for this notebook (gh #211): `./a.ipynb`,
  // `sub/./b.ipynb`, and an absolute path inside the serving root.
  const targets = candidatePaths(path, servingRoot);
  if (targets.length === 0) {
    return outcome;
  }
  const reverts: Array<{ path: string; done: Promise<void> }> = [];
  const seen = new Set<any>();
  for (const widget of shell.widgets('main')) {
    const context = (widget as any).context;
    if (!context || seen.has(context) || typeof context.revert !== 'function') {
      continue;
    }
    seen.add(context); // several views can share one context
    const open = String(context.path ?? '');
    if (!targets.some(target => samePath(open, target, servingRoot))) {
      continue;
    }
    if (context.model?.dirty) {
      outcome.skippedDirty += 1;
      continue;
    }
    reverts.push({ path: open, done: context.revert() });
  }
  const results = await Promise.allSettled(reverts.map(r => r.done));
  results.forEach((r, i) => {
    if (r.status === 'rejected') {
      console.warn(`langstage-jupyter: could not reload ${reverts[i].path}:`, r.reason);
    } else {
      outcome.reloaded += 1;
    }
  });
  return outcome;
}
