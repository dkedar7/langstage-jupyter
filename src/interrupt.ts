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
 * An older keyed-dict request may name a `tool`. A bare `interrupt("...")` value
 * arrives as a plain string: the text is the whole request (gh #173).
 */

/** One row of the approval card: the action the agent wants to take. */
export interface ActionSummary {
  name: string;
  description?: string;
  args?: unknown;
  /** What the agent asked, from `args.question` (e.g. the demo's `ask_user`). */
  question?: string;
  /** A plain-string request's text, shown as-is with no action name. */
  text?: string;
}

export const UNNAMED_ACTION = 'an action';

export function summarizeActions(actionRequests: unknown): ActionSummary[] {
  const requests = Array.isArray(actionRequests) ? actionRequests : [];
  const out: ActionSummary[] = [];
  for (const raw of requests) {
    if (typeof raw === 'string') {
      if (raw) {
        out.push({ name: '', text: raw });
      }
      continue;
    }
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
    const args = src.args;
    const question =
      args && typeof args === 'object' ? (args as Record<string, unknown>).question : undefined;
    out.push({
      name: String(name),
      description:
        typeof description === 'string' && description ? description : undefined,
      args,
      question: typeof question === 'string' && question ? question : undefined
    });
  }
  return out.length ? out : [{ name: UNNAMED_ACTION }];
}

/**
 * The args worth showing on the card under the question: everything but the
 * `question` itself, which the card already shows as text. `undefined` when nothing
 * is left.
 */
export function displayArgs(action: ActionSummary): Record<string, unknown> | undefined {
  const args = action.args;
  if (!args || typeof args !== 'object' || Array.isArray(args)) {
    return undefined;
  }
  const rest: Record<string, unknown> = { ...(args as Record<string, unknown>) };
  if (action.question !== undefined) {
    delete rest.question;
  }
  return Object.keys(rest).length ? rest : undefined;
}

/** The decision that answers the agent's question with the user's text (gh #173). */
export interface RespondDecision {
  type: 'respond';
  message: string;
}

/**
 * Answer an interrupt with `text`: one `respond` decision per action request (a
 * single one when the interrupt lists none), the shape the web app sends
 * (langstage#195). Blank text answers nothing, so it returns no decisions.
 */
export function respondDecisions(actionRequests: unknown, text: string): RespondDecision[] {
  const message = text.trim();
  if (!message) {
    return [];
  }
  const count = Array.isArray(actionRequests) && actionRequests.length ? actionRequests.length : 1;
  return Array.from({ length: count }, () => ({ type: 'respond' as const, message }));
}
