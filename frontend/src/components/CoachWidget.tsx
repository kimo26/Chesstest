import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { chat } from "../api/client";
import { useCoach } from "../context/CoachContext";
import { useUser } from "../hooks/useUser";
import type { ToolCallTrace } from "../types";

/**
 * Persistent coach widget.
 *
 * Rendered once at the App level so it follows the user across every page.
 * The widget consumes ``pageContext`` from the CoachContext to know what
 * the user is currently looking at (current FEN, flashcard, puzzle, etc.)
 * and forwards it on every ``/api/chat`` call with ``use_agent: true``.
 *
 * The backend uses the conversation_id on every reply, which we persist
 * in localStorage so the coach remembers the user across reloads and page
 * navigation — on a single local install this gives the feeling of one
 * long-running coaching relationship.
 */

interface WidgetMessage {
  role: "user" | "assistant";
  text: string;
  trace?: ToolCallTrace[] | null;
}

const CONV_KEY = "coach.conversation_id";

function loadConv(): number | null {
  try {
    const v = localStorage.getItem(CONV_KEY);
    return v ? Number(v) : null;
  } catch {
    return null;
  }
}

function saveConv(id: number | null) {
  try {
    if (id == null) localStorage.removeItem(CONV_KEY);
    else localStorage.setItem(CONV_KEY, String(id));
  } catch {
    // ignore
  }
}

function describeContext(ctx: ReturnType<typeof useCoach>["pageContext"]): string {
  if (!ctx) return "";
  switch (ctx.page) {
    case "practice":
      return `Practice${ctx.opponent ? ` vs ${ctx.opponent}` : ""}${
        ctx.last_move ? ` — last move ${ctx.last_move}` : ""
      }`;
    case "flashcards":
      return `Flashcards${ctx.card_type ? ` • ${ctx.card_type}` : ""}`;
    case "puzzles":
      return `Puzzle${ctx.rating ? ` • ${ctx.rating}` : ""}`;
    case "openings":
      return ctx.opening_name ? `Openings • ${ctx.opening_name}` : "Openings";
    case "insights":
      return "Insights";
    case "progress":
      return "Progress";
    case "dashboard":
      return "Dashboard";
    case "chat":
      return "Chat";
    default:
      return "";
  }
}

export default function CoachWidget() {
  const { user } = useUser();
  const { pageContext, isOpen, setIsOpen } = useCoach();

  const [messages, setMessages] = useState<WidgetMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [convId, setConvId] = useState<number | null>(() => loadConv());
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  const send = async () => {
    const q = input.trim();
    if (!q || loading) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", text: q }]);
    setLoading(true);
    try {
      const ctxFen =
        pageContext && "fen" in pageContext && typeof pageContext.fen === "string"
          ? pageContext.fen
          : null;
      const res = await chat({
        user_id: user.id,
        question: q,
        fen: ctxFen,
        conversation_id: convId,
        use_agent: true,
        page_context: pageContext,
      });
      if (res.conversation_id) {
        setConvId(res.conversation_id);
        saveConv(res.conversation_id);
      }
      setMessages((m) => [
        ...m,
        { role: "assistant", text: res.answer, trace: res.tool_trace },
      ]);
    } catch (err) {
      setMessages((m) => [
        ...m,
        { role: "assistant", text: `Error: ${err}` },
      ]);
    } finally {
      setLoading(false);
    }
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  const resetConv = () => {
    if (!confirm("Forget the coach's memory of this conversation?")) return;
    setMessages([]);
    setConvId(null);
    saveConv(null);
  };

  if (!isOpen) {
    return (
      <button
        className="coach-widget__fab"
        onClick={() => setIsOpen(true)}
        aria-label="Open coach"
      >
        💬 Coach
      </button>
    );
  }

  return (
    <div className="coach-widget">
      <div className="coach-widget__header">
        <span className="coach-widget__dot" />
        <span className="coach-widget__title">Coach</span>
        {pageContext && (
          <span className="coach-widget__ctx">{describeContext(pageContext)}</span>
        )}
        <button
          className="coach-widget__btn"
          onClick={resetConv}
          title="Start a new conversation"
        >
          New
        </button>
        <button
          className="coach-widget__btn"
          onClick={() => setIsOpen(false)}
          title="Minimise"
        >
          —
        </button>
      </div>

      <div className="coach-widget__messages">
        {messages.length === 0 && (
          <div className="coach-widget__empty">
            I remember our past chats. Ask "why was that move bad?", "what
            should I study next?", or "explain this card".
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`coach-widget__msg coach-widget__msg--${m.role}`}>
            <div className="coach-widget__msg-text">{m.text}</div>
            {m.trace && m.trace.length > 0 && (
              <details className="coach-widget__trace">
                <summary>
                  reasoning · {m.trace.length} tool call
                  {m.trace.length > 1 ? "s" : ""}
                </summary>
                <ul>
                  {m.trace.map((t, j) => (
                    <li key={j}>
                      <code>{t.name}</code>
                      {Object.keys(t.args || {}).length > 0 && (
                        <span className="coach-widget__args">
                          ({Object.keys(t.args).join(", ")})
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        ))}
        {loading && (
          <div className="coach-widget__msg coach-widget__msg--assistant">
            <div className="coach-widget__typing">Thinking…</div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="coach-widget__input-row">
        <textarea
          className="coach-widget__input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKey}
          placeholder="Ask your coach…"
          rows={2}
          disabled={loading}
        />
        <button
          className="coach-widget__send"
          onClick={send}
          disabled={loading || !input.trim()}
        >
          Send
        </button>
      </div>
    </div>
  );
}
