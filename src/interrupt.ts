/**
 * Read the requested action(s) out of an interrupt's `action_requests` (gh #168).
 *
 * Plain logic with no JupyterLab import. Mirrors langstage-vscode's `summarizeActions`
 * (vscode#101), since the same langstage-core interrupt frames reach both surfaces.
 * Three request shapes reach the wire:
 *
 * - HumanInTheLoopMiddleware: `{"name", "args", "description"}`;
 * - a HumanInterrupt list unwrapped by core: `{"action", "args"}` (e.g. the demo-tools
 *   agent's `ask_user`);
 * - a single `interrupt({...})` object, nested:
 *   `{"action_request": {"action", "args"}, "description"}`.
 *
 * An older keyed-dict request may name a `tool`.
 */

/** One row of the approval card: the action the agent wants to take. */
export interface ActionSummary {
  name: string;
  description?: string;
  args?: unknown;
}

export const UNNAMED_ACTION = 'an action';

export function summarizeActions(actionRequests: unknown): ActionSummary[] {
  const requests = Array.isArray(actionRequests) ? actionRequests : [];
  const out: ActionSummary[] = [];
  for (const raw of requests) {
    if (!raw || typeof raw !== 'object') {
      continue;
    }
    const req = raw as Record<string, unknown>;
    const nested =
      req.action_request && typeof req.action_request === 'object'
        ? (req.action_request as Record<string, unknown>)
        : undefined;
    const src = nested ?? req;
    const name = src.name ?? src.action ?? req.tool ?? UNNAMED_ACTION;
    const description = req.description ?? src.description;
    out.push({
      name: String(name),
      description:
        typeof description === 'string' && description ? description : undefined,
      args: src.args
    });
  }
  return out.length ? out : [{ name: UNNAMED_ACTION }];
}
