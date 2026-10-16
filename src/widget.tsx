import React, { useState, useEffect, useRef } from 'react';
import { ReactWidget } from '@jupyterlab/apputils';
import { JupyterFrontEnd } from '@jupyterlab/application';
import { IFileBrowserFactory } from '@jupyterlab/filebrowser';
import { NotebookPanel } from '@jupyterlab/notebook';
import { postStream, requestAPI } from './handler';
import { AgentWriteTracker, reloadOpenDocument } from './reload';
import {
  displayArgs,
  editDecisions,
  respondDecisions,
  summarizeActions,
  uniformDecisions
} from './interrupt';
import { SSELineBuffer, TodoItem, ToolCall, TurnAccumulator } from './stream';
import ReactMarkdown from 'react-markdown';
import { Send, RotateCw, Trash2, Square, Circle, CheckCircle2, ArrowRight } from 'lucide-react';

/**
 * Action request interface for interrupts
 */
interface ActionRequest {
  // The name arrives as `name`, `action`, a nested `action_request.action` or `tool`
  // depending on the interrupt shape; read it with `summarizeActions` (gh #168).
  name?: string;
  action?: string;
  tool?: string;
  action_request?: { action?: string; name?: string; args?: Record<string, any> };
  tool_call_id?: string;
  args?: Record<string, any>;
  description?: string;
}

/**
 * Review config interface
 */
interface ReviewConfig {
  allowed_decisions: string[];
}

/**
 * Interrupt data interface
 */
interface InterruptData {
  action_requests: ActionRequest[];
  review_configs?: ReviewConfig[];
  /**
   * The verbs this interrupt permits, normalized by langstage-core across interrupt
   * shapes (HumanInterrupt list, HITL middleware review_configs, plain dict). The
   * HumanInterrupt list shape has no review_configs at all, so this is the field to read.
   */
  allowed_decisions?: string[];
}

/**
 * The decisions an interrupt allows: core's top-level `allowed_decisions` (accurate since
 * langstage-core 1.0.36), else the first review_config's list for an older core.
 */
function allowedDecisions(interrupt?: InterruptData): string[] {
  if (!interrupt) {
    return [];
  }
  if (Array.isArray(interrupt.allowed_decisions)) {
    return interrupt.allowed_decisions;
  }
  return interrupt.review_configs?.[0]?.allowed_decisions ?? [];
}

/**
 * Decision interface
 */
interface Decision {
  type: 'approve' | 'reject' | 'edit' | 'respond';
  args?: Record<string, any>;
  /** An `edit` decision's replacement call (gh #175). */
  edited_action?: { name: string; args: Record<string, unknown> };
  explanation?: string;
  /** A `respond` decision's answer to the agent's question (gh #173). */
  message?: string;
}

/**
 * Interrupt card props
 */
interface InterruptCardProps {
  interrupt: InterruptData;
  /** False once the interrupt is answered, or while a turn is running. */
  active: boolean;
  onDecide: (decisions: Decision[]) => void;
}

/**
 * The approval card for one interrupt: what the agent asks, and a control for each
 * decision the interrupt allows. When `respond` is allowed (the agent asked the user
 * something), it shows the question and a text field with a Respond button, like the
 * web app (gh #173, langstage#195).
 */
const InterruptCard: React.FC<InterruptCardProps> = ({ interrupt, active, onDecide }) => {
  const [response, setResponse] = useState('');
  const allowed = allowedDecisions(interrupt);
  const actions = summarizeActions(interrupt.action_requests);

  // Edit request `idx`'s args in a prompt, then approve the rest (gh #175).
  const handleEdit = (idx: number) => {
    const newArgsStr = prompt(
      'Edit arguments (JSON):',
      JSON.stringify(actions[idx]?.args ?? {}, null, 2)
    );
    if (newArgsStr) {
      try {
        onDecide(editDecisions(interrupt.action_requests, idx, JSON.parse(newArgsStr)));
      } catch (e) {
        alert('Invalid JSON');
      }
    }
  };
  // With several requests, each row gets its own Edit button.
  const editPerAction = allowed.includes('edit') && actions.length > 1;

  const handleRespond = () => {
    const decisions = respondDecisions(interrupt.action_requests, response);
    if (decisions.length) {
      onDecide(decisions);
    }
  };

  return (
    <div className="deepagents-interrupt">
      <div className="deepagents-interrupt-header">
        Approval required
      </div>
      {actions.map((action, idx) => {
        const args = displayArgs(action);
        return (
          <div key={idx} className="deepagents-interrupt-description">
            {action.text !== undefined ? (
              <div className="deepagents-interrupt-question">{action.text}</div>
            ) : (
              <>
                Tool: <strong>{action.name}</strong>
                {action.description && (
                  <div className="deepagents-interrupt-action-description">
                    {action.description}
                  </div>
                )}
                {action.question && (
                  <div className="deepagents-interrupt-question">{action.question}</div>
                )}
                {args && (
                  <pre className="deepagents-interrupt-args">
                    {JSON.stringify(args, null, 2)}
                  </pre>
                )}
                {editPerAction && (
                  <button
                    className="deepagents-decision-btn deepagents-edit-btn"
                    onClick={() => handleEdit(idx)}
                    disabled={!active}
                  >
                    Edit
                  </button>
                )}
              </>
            )}
          </div>
        );
      })}
      {allowed.includes('respond') && (
        <form
          className="deepagents-interrupt-respond"
          onSubmit={e => {
            e.preventDefault();
            handleRespond();
          }}
        >
          <input
            type="text"
            className="deepagents-interrupt-response"
            aria-label="Your response"
            placeholder="Type your response..."
            value={response}
            onChange={e => setResponse(e.target.value)}
            disabled={!active}
          />
          <button
            type="submit"
            className="deepagents-decision-btn deepagents-respond-btn"
            disabled={!active || !response.trim()}
          >
            Respond
          </button>
        </form>
      )}
      <div className="deepagents-action-decisions">
        {allowed.includes('approve') && (
          <button
            className="deepagents-decision-btn deepagents-approve-btn"
            onClick={() => onDecide(uniformDecisions(interrupt.action_requests, 'approve'))}
            disabled={!active}
          >
            Approve
          </button>
        )}
        {allowed.includes('reject') && (
          <button
            className="deepagents-decision-btn deepagents-reject-btn"
            onClick={() => onDecide(uniformDecisions(interrupt.action_requests, 'reject'))}
            disabled={!active}
          >
            Reject
          </button>
        )}
        {allowed.includes('edit') && !editPerAction && (
          <button
            className="deepagents-decision-btn deepagents-edit-btn"
            onClick={() => handleEdit(0)}
            disabled={!active}
          >
            Edit
          </button>
        )}
      </div>
    </div>
  );
};

/**
 * Message interface for chat messages
 */
interface Message {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: Date;
  error?: boolean;
  intermediates?: string[];
  reasoning?: string;
  toolCalls?: ToolCall[];
  todoList?: TodoItem[];
  interrupt?: InterruptData;
}

/**
 * Chat component props
 */
interface ChatComponentProps {
  shell: JupyterFrontEnd.IShell | null;
  browserFactory: IFileBrowserFactory | null;
  onAgentNameChange?: (name: string) => void;
}

/**
 * Chat component
 */
const ChatComponent: React.FC<ChatComponentProps> = ({ shell, browserFactory, onAgentNameChange }) => {
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [agentStatus, setAgentStatus] = useState<'unknown' | 'healthy' | 'needs_setup' | 'error'>('unknown');
  const [agentStatusMessage, setAgentStatusMessage] = useState<string>('');
  const [agentName, setAgentName] = useState<string>('LangStage');
  const [threadId, setThreadId] = useState<string>(() => crypto.randomUUID());
  // The assistant message whose interrupt awaits a decision. Only that card's
  // controls are live, so an answered card can't resume a later interrupt (gh #173).
  const [pendingInterruptId, setPendingInterruptId] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Lazy load agent - delay health check to avoid blocking JupyterLab startup
  useEffect(() => {
    const timer = setTimeout(() => {
      checkAgentHealth();
    }, 2000); // Wait 2 seconds to let JupyterLab finish loading

    return () => clearTimeout(timer);
  }, []);

  const checkAgentHealth = async () => {
    try {
      const response = await requestAPI<any>('health', {
        method: 'GET'
      });

      // `ready` (not merely `agent_loaded`) gates the 🟢 indicator: the agent can
      // actually run a turn. A loaded-but-not-ready agent — an uncompiled graph, or the
      // default agent with no provider key — shows a distinct 🟠 state with an actionable
      // message instead of a green "ready" that fails on the first turn (gh #60).
      const message = response.message || '';
      setAgentStatusMessage(message);
      if (response.ready) {
        setAgentStatus('healthy');

        // Update agent name if available
        const name = response.agent_name || 'LangStage';
        setAgentName(name);
        if (onAgentNameChange) {
          onAgentNameChange(name);
        }

        addSystemMessage('Agent is ready and connected');
      } else if (response.agent_loaded) {
        // Loaded, but not runnable yet (e.g. missing API key) — amber, not green.
        setAgentStatus('needs_setup');
        if (response.agent_name) {
          setAgentName(response.agent_name);
        }
        addSystemMessage(message || 'Agent loaded but not ready to run a turn.');
      } else {
        setAgentStatus('error');
        addSystemMessage(message || 'Agent not loaded. Please ensure agent.py is configured correctly.');
      }
    } catch (error) {
      console.error('Error checking agent health:', error);
      setAgentStatus('error');
      setAgentStatusMessage('Failed to connect to agent service');
      addSystemMessage('Failed to connect to agent service');
    }
  };

  const addSystemMessage = (content: string) => {
    const systemMessage: Message = {
      id: Date.now().toString(),
      role: 'system',
      content,
      timestamp: new Date()
    };
    setMessages(prev => [...prev, systemMessage]);
  };

  // Reload an open notebook tab once an agent tool that wrote it returns, so the
  // tab's next save can't write a stale copy over the agent's cells and outputs
  // (gh #160). The tracker outlives one stream: an interrupt can split a tool call
  // (send stream) from its result (resume stream).
  const writeTracker = useRef(new AgentWriteTracker());
  const reloadAgentWrite = async (path: string) => {
    const { skippedDirty } = await reloadOpenDocument(shell, path);
    if (skippedDirty > 0) {
      addSystemMessage(
        `The agent changed ${path}, but its open tab has unsaved changes, so it was ` +
          "not reloaded. Saving that tab would overwrite the agent's version. To see " +
          "the agent's version, use File > Reload Notebook from Disk (this discards " +
          'the unsaved changes).'
      );
    }
  };
  const syncAgentWrites = (data: any) => {
    const tracker = writeTracker.current;
    if (Array.isArray(data.tool_calls)) {
      tracker.noteCalls(data.tool_calls);
    }
    if (data.tool_result !== undefined) {
      const path = tracker.takeResult(data.id);
      if (path) {
        void reloadAgentWrite(path);
      }
    }
    // A finished turn (not one paused on an interrupt, whose pending call may still
    // run after the resume) reloads anything whose result frame never arrived.
    const ended =
      (data.status === 'complete' && data.outcome !== 'interrupted') ||
      data.status === 'error' ||
      data.status === 'cancelled';
    if (ended) {
      tracker.takeAll().forEach(path => void reloadAgentWrite(path));
    }
  };

  // Put `fields` on the assistant message `id`, creating it if it isn't shown yet.
  const upsertAssistantMessage = (id: string, fields: Partial<Message>) => {
    setMessages(prev =>
      prev.some(m => m.id === id)
        ? prev.map(m => (m.id === id ? { ...m, ...fields } : m))
        : [
            ...prev,
            { id, role: 'assistant' as const, content: '', timestamp: new Date(), ...fields }
          ]
    );
  };

  // Show a failed request (e.g. a non-2xx status) as the turn's error message,
  // rather than ending the turn with nothing (gh #170).
  const showTurnError = (id: string, error: unknown, fallback: string) => {
    const errorMessage: Message = {
      id,
      role: 'assistant',
      content: `Error: ${error instanceof Error ? error.message : fallback}`,
      timestamp: new Date(),
      error: true
    };
    setMessages(prev =>
      prev.some(m => m.id === id)
        ? prev.map(m => (m.id === id ? errorMessage : m))
        : [...prev, errorMessage]
    );
  };

  /**
   * Read one turn's SSE stream (from /chat or /resume) into the assistant message
   * `assistantMessageId`. The first streaming or interrupt frame creates it.
   */
  const readTurnStream = async (
    response: Response,
    assistantMessageId: string,
    label: string
  ) => {
    if (!response.body) {
      throw new Error('No response body');
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    const lines = new SSELineBuffer();
    const turn = new TurnAccumulator();

    const turnFields = (): Partial<Message> => {
      const t = turn.snapshot();
      return {
        content: t.content,
        intermediates: t.intermediates,
        reasoning: t.reasoning || undefined,
        toolCalls: t.toolCalls.length > 0 ? t.toolCalls : undefined,
        todoList: t.todoList.length > 0 ? t.todoList : undefined
      };
    };

    const handleFrame = (data: any) => {
      console.log(`${label} SSE data:`, data);
      syncAgentWrites(data);

      if (data.status === 'streaming') {
        turn.add(data);
        upsertAssistantMessage(assistantMessageId, turnFields());
      } else if (data.status === 'interrupt') {
        // Show the decision UI.
        setPendingInterruptId(assistantMessageId);
        upsertAssistantMessage(assistantMessageId, { interrupt: data.interrupt });
      } else if (data.status === 'complete') {
        // The message keeps its intermediates, reasoning and tool calls.
        console.log(`${label} stream complete`);
      } else if (data.status === 'cancelled') {
        addSystemMessage(data.message || 'Execution cancelled by user');
      } else if (data.status === 'error') {
        console.error(`${label} stream error:`, data.error);
        setMessages(prev => [
          ...prev,
          {
            id: `${assistantMessageId}-error`,
            role: 'assistant' as const,
            content: data.error || 'An error occurred',
            timestamp: new Date(),
            error: true
          }
        ]);
      }
    };

    while (true) {
      const { done, value } = await reader.read();
      const payloads = done
        ? lines.end()
        : lines.push(decoder.decode(value, { stream: true }));
      for (const payload of payloads) {
        try {
          handleFrame(JSON.parse(payload));
        } catch (e) {
          console.error(`Error parsing ${label} SSE data:`, e);
        }
      }
      if (done) {
        break;
      }
    }
  };

  const handleSendMessage = async () => {
    if (!inputValue.trim() || isLoading) return;

    const userMessage: Message = {
      id: Date.now().toString(),
      role: 'user',
      content: inputValue,
      timestamp: new Date()
    };

    setMessages(prev => [...prev, userMessage]);
    const savedInput = inputValue;
    setInputValue('');
    setIsLoading(true);

    // Get current directory and focused widget
    let currentDirectory = '';
    let focusedWidget = '';

    if (browserFactory) {
      const browser = browserFactory.tracker.currentWidget;
      if (browser) {
        currentDirectory = browser.model.path;
      }
    }

    // Get the currently focused widget from the shell
    if (shell && shell.currentWidget) {
      const current = shell.currentWidget;
      const title = current.title.label;

      // Check if it has a context (files like notebooks, text files, etc.)
      if ((current as any).context?.path) {
        focusedWidget = (current as any).context.path;
      } else if (title === 'Launcher') {
        focusedWidget = 'JupyterLab Launcher';
      } else if (title.startsWith('Terminal')) {
        focusedWidget = `Terminal: ${title}`;
      } else if (title) {
        // Generic widget with a title
        focusedWidget = title;
      }
    }

    // Get text selection from notebook or editor
    let selectedText = '';
    let selectionMetadata = '';
    if (shell && shell.currentWidget) {
      const current = shell.currentWidget;

      // Check if it's a notebook
      if (current instanceof NotebookPanel) {
        const notebook = current.content;
        const activeCell = notebook.activeCell;

        if (activeCell && activeCell.editor) {
          const editor = activeCell.editor;
          const selection = editor.getSelection();

          if (selection.start.line !== selection.end.line ||
              selection.start.column !== selection.end.column) {
            selectedText = editor.model.sharedModel.getSource().substring(
              editor.getOffsetAt(selection.start),
              editor.getOffsetAt(selection.end)
            );

            // Get cell index
            const cells = notebook.model?.cells;
            let cellIndex = -1;
            if (cells) {
              for (let i = 0; i < cells.length; i++) {
                if (cells.get(i) === activeCell.model) {
                  cellIndex = i;
                  break;
                }
              }
            }

            if (cellIndex >= 0) {
              selectionMetadata = `cell_${cellIndex}`;
            }
          }
        }
      }
      // Check if it's a file editor (text, python, etc.)
      else if ((current as any).content?.editor) {
        const editor = (current as any).content.editor;
        const selection = editor.getSelection();

        if (selection.start.line !== selection.end.line ||
            selection.start.column !== selection.end.column) {
          selectedText = editor.model.sharedModel.getSource().substring(
            editor.getOffsetAt(selection.start),
            editor.getOffsetAt(selection.end)
          );

          // Get line numbers (1-indexed for user display)
          const startLine = selection.start.line + 1;
          const endLine = selection.end.line + 1;
          if (startLine === endLine) {
            selectionMetadata = `line_${startLine}`;
          } else {
            selectionMetadata = `lines_${startLine}-${endLine}`;
          }
        }
      }
    }

    console.log('Sending message with thread_id:', threadId);
    console.log('Current directory:', currentDirectory);
    console.log('Focused widget:', focusedWidget);
    console.log('Selected text:', selectedText ? `${selectedText.length} characters at ${selectionMetadata}` : 'none');

    // The turn's reply streams into this assistant message.
    const assistantMessageId = (Date.now() + 1).toString();

    try {
      const response = await postStream('chat', {
        message: savedInput,
        stream: true,
        thread_id: threadId,
        current_directory: currentDirectory,
        focused_widget: focusedWidget,
        selected_text: selectedText,
        selection_metadata: selectionMetadata
      });
      await readTurnStream(response, assistantMessageId, 'Chat');
    } catch (error) {
      console.error('Error sending message:', error);
      showTurnError(assistantMessageId, error, 'Failed to send message');
    } finally {
      setIsLoading(false);
      inputRef.current?.focus();
    }
  };

  const handleResumeFromInterrupt = async (decisions: Decision[]) => {
    setPendingInterruptId(null);
    setIsLoading(true);
    const assistantMessageId = (Date.now() + 1).toString();

    try {
      const response = await postStream('resume', {
        decisions: decisions,
        thread_id: threadId
      });
      await readTurnStream(response, assistantMessageId, 'Resume');
    } catch (error) {
      console.error('Error resuming from interrupt:', error);
      showTurnError(assistantMessageId, error, 'Failed to resume');
    } finally {
      setIsLoading(false);
      inputRef.current?.focus();
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  const handleReloadAgent = async () => {
    setIsLoading(true);
    try {
      await requestAPI<any>('reload', {
        method: 'POST'
      });
      addSystemMessage('Agent reloaded successfully');
      await checkAgentHealth();
    } catch (error) {
      console.error('Error reloading agent:', error);
      addSystemMessage('Failed to reload agent');
    } finally {
      setIsLoading(false);
    }
  };

  const cancelExecution = async () => {
    try {
      await requestAPI<any>('cancel', {
        method: 'POST',
        body: JSON.stringify({ thread_id: threadId })
      });
      console.log('Cancellation requested for thread:', threadId);
    } catch (error) {
      console.error('Error cancelling execution:', error);
    }
  };

  const clearChat = () => {
    setMessages([]);
    setPendingInterruptId(null);
    // Generate new thread_id for fresh conversation
    setThreadId(crypto.randomUUID());
  };

  const formatTime = (date: Date) => {
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  };

  const formatToolArgs = (args: Record<string, any>, maxLength: number = 150): string => {
    const truncated: Record<string, any> = {};
    for (const [key, value] of Object.entries(args)) {
      const strValue = JSON.stringify(value);
      if (strValue.length > maxLength) {
        truncated[key] = strValue.substring(0, maxLength) + '...';
      } else {
        truncated[key] = value;
      }
    }
    return JSON.stringify(truncated);
  };

  return (
    <div className="deepagents-chat-container">
      {/* Header */}
      <div className="deepagents-chat-header">
        <h2 className="deepagents-chat-title">{agentName}</h2>
        <div className="deepagents-chat-controls">
          <span
            className={`deepagents-status-indicator deepagents-status-${agentStatus}`}
            title={
              agentStatusMessage ||
              (agentStatus === 'healthy' ? 'Agent connected' : 'Agent not available')
            }
          />
          <button
            className="deepagents-icon-button"
            onClick={handleReloadAgent}
            disabled={isLoading}
            title="Reload agent"
          >
            <RotateCw size={14} />
          </button>
          <button
            className="deepagents-icon-button"
            onClick={clearChat}
            title="Clear chat"
          >
            <Trash2 size={14} />
          </button>
        </div>
      </div>

      {/* Messages */}
      <div className="deepagents-chat-messages">
        {messages.length === 0 ? (
          <div className="deepagents-chat-empty">
            <p>Start a conversation with your agent</p>
          </div>
        ) : (
          messages.map(message => (
            <div
              key={message.id}
              className={`deepagents-message deepagents-message-${message.role} ${
                message.error ? 'deepagents-message-error' : ''
              }`}
            >
              <div className="deepagents-message-header">
                <span className="deepagents-message-role">
                  {message.role === 'user' ? 'You' : message.role === 'assistant' ? 'Agent' : 'System'}
                </span>
                <span className="deepagents-message-time">
                  {formatTime(message.timestamp)}
                </span>
              </div>
              <div className="deepagents-message-content">
                {message.reasoning && (
                  <details className="deepagents-reasoning">
                    <summary className="deepagents-reasoning-summary">Thinking</summary>
                    <div className="deepagents-reasoning-content">{message.reasoning}</div>
                  </details>
                )}
                {message.intermediates && message.intermediates.length > 1 && (
                  <div className="deepagents-message-intermediates">
                    {message.intermediates.slice(0, -1).map((intermediate, idx) => (
                      <div key={idx} className="deepagents-intermediate-message">
                        {intermediate}
                      </div>
                    ))}
                  </div>
                )}
                {message.toolCalls && message.toolCalls.length > 0 && (
                  <div className="deepagents-tool-calls">
                    {message.toolCalls.map((toolCall, idx) => (
                      <details key={idx} className="deepagents-tool-call">
                        <summary className="deepagents-tool-call-summary">
                          {toolCall.name}
                        </summary>
                        <div className="deepagents-tool-call-args">
                          {formatToolArgs(toolCall.args)}
                        </div>
                      </details>
                    ))}
                  </div>
                )}
                {message.todoList && message.todoList.length > 0 && (
                  <div className="deepagents-todo-list">
                    {message.todoList.map((todo, idx) => (
                      <div key={idx} className={`deepagents-todo-item deepagents-todo-${todo.status}`}>
                        <span className="deepagents-todo-marker">
                          {todo.status === 'completed' ? (
                            <CheckCircle2 size={16} />
                          ) : todo.status === 'in_progress' ? (
                            <ArrowRight size={16} />
                          ) : (
                            <Circle size={16} />
                          )}
                        </span>
                        <span className="deepagents-todo-content">{todo.content}</span>
                      </div>
                    ))}
                  </div>
                )}
                {message.interrupt && (
                  <InterruptCard
                    interrupt={message.interrupt}
                    active={pendingInterruptId === message.id && !isLoading}
                    onDecide={handleResumeFromInterrupt}
                  />
                )}
                {message.role === 'assistant' ? (
                  <ReactMarkdown>{message.content}</ReactMarkdown>
                ) : (
                  message.content
                )}
              </div>
            </div>
          ))
        )}
        {isLoading && (
          <div className="deepagents-message deepagents-message-assistant">
            <div className="deepagents-message-header">
              <span className="deepagents-message-role">Agent</span>
            </div>
            <div className="deepagents-message-content deepagents-typing-indicator">
              <span></span>
              <span></span>
              <span></span>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className="deepagents-chat-input-container">
        <textarea
          ref={inputRef}
          className="deepagents-chat-input"
          placeholder="Type your message... (Shift+Enter for new line)"
          value={inputValue}
          onChange={e => setInputValue(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={isLoading}
          rows={1}
        />
        {isLoading ? (
          <button
            className="deepagents-stop-button"
            onClick={cancelExecution}
            title="Stop execution"
            aria-label="Stop execution"
          >
            <Square size={16} fill="currentColor" />
          </button>
        ) : (
          <button
            className="deepagents-send-button"
            onClick={handleSendMessage}
            disabled={!inputValue.trim()}
            title="Send message"
            aria-label="Send message"
          >
            <Send size={18} />
          </button>
        )}
      </div>
    </div>
  );
};

/**
 * A Lumino Widget that wraps a ChatComponent.
 */
export class ChatWidget extends ReactWidget {
  private shell: JupyterFrontEnd.IShell | null;
  private browserFactory: IFileBrowserFactory | null;

  constructor(
    shell: JupyterFrontEnd.IShell | null = null,
    browserFactory: IFileBrowserFactory | null = null
  ) {
    super();
    this.addClass('deepagents-chat-widget');
    this.shell = shell;
    this.browserFactory = browserFactory;
  }

  /**
   * Update the widget title with the agent name
   * Note: We don't update title.label to keep the sidebar showing only the icon
   */
  updateAgentName(name: string): void {
    // Don't update title.label - we want icon-only in sidebar
    // The agent name will still appear in the chat header
  }

  render(): JSX.Element {
    return <ChatComponent
      shell={this.shell}
      browserFactory={this.browserFactory}
      onAgentNameChange={(name) => this.updateAgentName(name)}
    />;
  }
}
