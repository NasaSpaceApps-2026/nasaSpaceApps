import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import './chat.css';
import { Markdown } from './markdown';
import { ToolCard } from './ToolCard';
import { DEMO_PROMPTS, followUps } from './prompts';
import { DEFAULT_AGENT_URL, useChat, type UseChatOptions } from './useChat';
import type { ChatMessage } from './types';
import type { Feature } from './features';
import { describeHealth, friendlyError } from './friendly';
import { useAgentHealth } from './useAgentHealth';

export interface ChatPanelProps extends UseChatOptions {
  /** Override the suggested prompts shown when the conversation is empty. */
  suggestedPrompts?: string[];
  /** When given, tool cards list their result rows and clicking one calls this (the dock flies the camera there). */
  onFocusPlace?: (f: Feature) => void;
}

export function ChatPanel({ agentUrl, getContext, onToolEvent, suggestedPrompts = DEMO_PROMPTS, onFocusPlace }: ChatPanelProps) {
  const { state, send, stop, clear } = useChat({ agentUrl, getContext, onToolEvent });
  const { health, down } = useAgentHealth(agentUrl ?? DEFAULT_AGENT_URL);
  const status = describeHealth(health, down);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const [draft, setDraft] = useState('');
  const logRef = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const { messages, streaming } = state;

  useEffect(() => {
    const el = logRef.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [messages]);

  const submit = (text = draft) => {
    if (!text.trim() || streaming) return;
    stick.current = true;
    send(text);
    setDraft('');
    inputRef.current?.focus(); // keep the keyboard in the box after clicking a suggested prompt or Send
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Escape' && streaming) {
      e.preventDefault();
      stop();
      return;
    }
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  const lastUser = [...messages].reverse().find((m) => m.role === 'user');
  const lastMsg = messages[messages.length - 1];
  const showFollowUps = !streaming && lastMsg?.role === 'assistant' && !lastMsg.error;

  return (
    <section className="ares-chat" aria-label="Ares copilot chat">
      <header className="ares-chat__header">
        <div className="ares-chat__title">
          <h2>Ares Copilot</h2>
          <span className={`ares-status ares-status--${status.level}`} role="status" title="Agent health (GET /health)">
            <span className="ares-status__dot" aria-hidden="true" />
            {status.label}
          </span>
        </div>
        <button type="button" className="ares-btn ares-btn--ghost" onClick={clear} disabled={messages.length === 0}>
          Clear
        </button>
      </header>

      {status.banner && (
        <div className="ares-banner" role="alert">
          <strong>{status.banner.title}</strong> {status.banner.fix}
        </div>
      )}

      <div
        className="ares-chat__log"
        ref={logRef}
        role="log"
        aria-label="Conversation"
        aria-live="polite"
        aria-relevant="additions text"
        tabIndex={0}
        onScroll={(e) => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
        }}
      >
        {messages.length === 0 && (
          <div className="ares-empty">
            <p className="ares-empty__lead">Ask me to plan an EVA or drive the rover at Jezero crater. You will see every tool I use.</p>
            <Chips prompts={suggestedPrompts} onPick={submit} label="Suggested prompts" />
          </div>
        )}
        {messages.map((m, i) => (
          <Message key={m.id} m={m} pending={streaming && i === messages.length - 1} onFocusPlace={onFocusPlace} />
        ))}
        {showFollowUps && (
          <Chips
            prompts={followUps(lastUser && lastUser.role === 'user' ? lastUser.text : undefined)}
            onPick={submit}
            label="Try next"
          />
        )}
      </div>

      <form
        className="ares-chat__input"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <label htmlFor="ares-chat-input" className="ares-sr">
          Message the copilot
        </label>
        <textarea
          ref={inputRef}
          id="ares-chat-input"
          rows={2}
          value={draft}
          placeholder="Ask the copilot…"
          title="Enter sends, Shift+Enter adds a line, Esc stops a running answer"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKey}
        />
        {streaming ? (
          <button type="button" className="ares-btn ares-btn--stop" onClick={stop}>
            Stop
          </button>
        ) : (
          <button type="submit" className="ares-btn ares-btn--primary" disabled={!draft.trim()}>
            Send
          </button>
        )}
      </form>
    </section>
  );
}

function Chips({ prompts, onPick, label }: { prompts: string[]; onPick: (p: string) => void; label: string }) {
  return (
    <div className="ares-chips" role="group" aria-label={label}>
      {prompts.map((p) => (
        <button key={p} type="button" className="ares-chip" onClick={() => onPick(p)}>
          {p}
        </button>
      ))}
    </div>
  );
}

function Message({ m, pending, onFocusPlace }: { m: ChatMessage; pending: boolean; onFocusPlace?: (f: Feature) => void }) {
  if (m.role === 'user') {
    return (
      <div className="ares-msg ares-msg--user">
        <span className="ares-sr">You: </span>
        {m.text}
      </div>
    );
  }
  const empty = m.parts.length === 0;
  return (
    <div className="ares-msg ares-msg--assistant" aria-busy={pending}>
      <span className="ares-sr">Copilot: </span>
      {m.parts.map((p, i) =>
        p.kind === 'text' ? (
          <div className="ares-text" key={i}>
            <Markdown text={p.text} />
          </div>
        ) : (
          <ToolCard key={p.call.id} call={p.call} onFocusPlace={onFocusPlace} />
        ),
      )}
      {pending && empty && <div className="ares-thinking">Thinking…</div>}
      {m.stopped && <div className="ares-note">Stopped.</div>}
      {m.error && <ErrorNote message={m.error} />}
    </div>
  );
}

function ErrorNote({ message }: { message: string }) {
  const f = friendlyError(message);
  return (
    <div className="ares-error" role="alert">
      <strong>{f.title}</strong> {f.fix}
      {f.detail && f.detail !== f.title && <div className="ares-error__detail">{f.detail}</div>}
    </div>
  );
}
