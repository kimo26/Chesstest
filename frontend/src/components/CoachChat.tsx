import { useState, useRef, useEffect, type KeyboardEvent } from "react";
import { chat } from "../api/client";
import type { ChatSource } from "../types";

interface Message {
  role: "user" | "assistant";
  text: string;
  sources?: ChatSource[];
}

interface Props {
  userId: number;
  fen?: string | null;
  conversationId?: number | null;
  onConversationId?: (id: number) => void;
  placeholder?: string;
  className?: string;
}

export default function CoachChat({
  userId,
  fen,
  conversationId: externalConvId,
  onConversationId,
  placeholder = "Ask your coach anything…",
  className = "",
}: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [convId, setConvId] = useState<number | null>(externalConvId ?? null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const send = async () => {
    const q = input.trim();
    if (!q || loading) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", text: q }]);
    setLoading(true);
    try {
      const res = await chat({
        user_id: userId,
        question: q,
        fen: fen ?? undefined,
        conversation_id: convId ?? undefined,
      });
      setConvId(res.conversation_id);
      onConversationId?.(res.conversation_id);
      setMessages((m) => [
        ...m,
        { role: "assistant", text: res.answer, sources: res.sources },
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

  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  return (
    <div className={`coach-chat ${className}`}>
      <div className="coach-chat__header">
        <span className="coach-chat__dot" />
        Coach
        {fen && <span className="coach-chat__fen-badge">board linked</span>}
      </div>

      <div className="coach-chat__messages">
        {messages.length === 0 && (
          <div className="coach-chat__empty">
            Ask about openings, plans, or the current position.
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`coach-chat__msg coach-chat__msg--${m.role}`}>
            <div className="coach-chat__msg-text">{m.text}</div>
            {m.sources && m.sources.length > 0 && (
              <details className="coach-chat__sources">
                <summary>{m.sources.length} source(s)</summary>
                <ul>
                  {m.sources.map((s) => (
                    <li key={s.doc_id}>
                      [{s.chunk_level}] {s.title} (score: {s.score.toFixed(3)})
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        ))}
        {loading && (
          <div className="coach-chat__msg coach-chat__msg--assistant">
            <div className="coach-chat__typing">Thinking…</div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="coach-chat__input-row">
        <textarea
          className="coach-chat__input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKey}
          placeholder={placeholder}
          rows={2}
          disabled={loading}
        />
        <button
          className="coach-chat__send"
          onClick={send}
          disabled={loading || !input.trim()}
        >
          Send
        </button>
      </div>
    </div>
  );
}
