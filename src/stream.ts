/**
 * Read the sidebar's SSE stream (from /chat or /resume) into one assistant turn.
 *
 * Plain logic with no JupyterLab import, shared by the chat and resume streams.
 */

/** A tool call the agent made, as a `tool_calls` frame carries it. */
export interface ToolCall {
  id: string;
  name: string;
  args: Record<string, any>;
}

/** One row of the agent's todo list. */
export interface TodoItem {
  content: string;
  status: 'pending' | 'in_progress' | 'completed';
  activeForm?: string;
}

/** What one assistant turn has streamed so far. */
export interface ITurn {
  /** The latest text run. Once the turn ends, this is the answer. */
  content: string;
  /** Every text run, in order. The runs before the last show as grey steps. */
  intermediates: string[];
  /** The model's reasoning, shown in a collapsed "Thinking" block (langstage#196). */
  reasoning: string;
  toolCalls: ToolCall[];
  todoList: TodoItem[];
}

/**
 * Split a `text/event-stream` body into the payloads of its `data:` lines.
 *
 * A network read can end mid-line, and a proxy (e.g. JupyterHub's) can re-chunk
 * the stream. A partial line is kept until the rest of it arrives, instead of
 * being parsed early and dropped.
 */
export class SSELineBuffer {
  private pending = '';

  /** Add the next decoded text. Returns the `data:` payloads it completed. */
  push(text: string): string[] {
    const lines = (this.pending + text).split('\n');
    this.pending = lines.pop() ?? '';
    return SSELineBuffer.payloads(lines);
  }

  /** The stream ended. Returns the payload of a last line with no newline. */
  end(): string[] {
    const rest = this.pending;
    this.pending = '';
    return SSELineBuffer.payloads([rest]);
  }

  private static payloads(lines: string[]): string[] {
    return lines
      .map(line => line.replace(/\r$/, ''))
      .filter(line => line.startsWith('data: '))
      .map(line => line.slice('data: '.length));
  }
}

/** Folds a turn's `status: "streaming"` frames into an {@link ITurn}. */
export class TurnAccumulator {
  private turn: ITurn = {
    content: '',
    intermediates: [],
    reasoning: '',
    toolCalls: [],
    todoList: []
  };
  // The node whose text run we're currently accumulating. AG-UI streams token
  // deltas, so consecutive same-node text chunks are one message (appended).
  private textNode: string | undefined;
  // The `message_id` of the current text run's frames, when they carry one.
  private textMessageId: unknown;
  // Text or a tool call arrived since the last reasoning delta.
  private reasoningInterrupted = false;

  /** Fold in one `status: "streaming"` frame. */
  add(frame: any): void {
    if (Array.isArray(frame.tool_calls)) {
      frame.tool_calls.forEach((tc: ToolCall) => this.turn.toolCalls.push({ ...tc }));
      // A tool break ends the current text run, so text after it starts a new
      // intermediate message.
      this.textNode = undefined;
      this.reasoningInterrupted = true;
    }

    if (frame.todo_list) {
      this.turn.todoList = frame.todo_list;
    }

    // Reasoning arrives as deltas, like text. They accumulate into one block per
    // turn. Reasoning that resumes after text or a tool call starts a new paragraph.
    if (typeof frame.reasoning === 'string' && frame.reasoning) {
      if (this.reasoningInterrupted && this.turn.reasoning) {
        this.turn.reasoning += '\n\n';
      }
      this.turn.reasoning += frame.reasoning;
      this.reasoningInterrupted = false;
    }

    // AG-UI streams token DELTAS (not cumulative per-message chunks), so
    // consecutive deltas of one message accumulate into one intermediate. A normal
    // reply then renders as a single clean block, while a node/tool break starts a new
    // intermediate (preserving the grey multi-step display). A change of `message_id`
    // is a break too: two AIMessages of one node are two runs, the rule core and the
    // other surfaces apply (langstage-core#200).
    if (frame.chunk) {
      const node = frame.node ?? '';
      const messageId = frame.message_id;
      const runs = this.turn.intermediates;
      const sameRun =
        this.textNode === node &&
        (messageId === undefined || messageId === null || messageId === this.textMessageId);
      if (sameRun && runs.length > 0) {
        runs[runs.length - 1] += frame.chunk;
      } else {
        runs.push(frame.chunk);
        this.textNode = node;
      }
      this.textMessageId = messageId;
      this.turn.content = runs[runs.length - 1];
      this.reasoningInterrupted = true;
    }
  }

  /** A copy of the turn so far, safe to put in React state. */
  snapshot(): ITurn {
    const t = this.turn;
    return {
      content: t.content,
      intermediates: [...t.intermediates],
      reasoning: t.reasoning,
      toolCalls: [...t.toolCalls],
      todoList: [...t.todoList]
    };
  }
}
