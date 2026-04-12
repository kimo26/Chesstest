import { useState } from "react";
import Chessboard from "../components/Chessboard";
import CoachChat from "../components/CoachChat";
import { useUser } from "../hooks/useUser";

export default function Chat() {
  const { user } = useUser();
  const [fen, setFen] = useState("");
  const [fenInput, setFenInput] = useState("");
  const [convId, setConvId] = useState<number | null>(null);

  const applyFen = () => {
    const f = fenInput.trim();
    if (f) setFen(f);
    else setFen("");
  };

  return (
    <div className="page chat-page">
      <h1>Coach Chat</h1>
      <p className="text-muted">
        Ask anything about chess openings, plans, or a specific position.
        Answers are grounded in Wikipedia-sourced opening theory.
      </p>

      <div className="chat-page__layout">
        <div className="chat-page__board-col">
          {fen && <Chessboard fen={fen} viewOnly width={400} height={400} />}
          <div className="chat-page__fen-input">
            <input
              value={fenInput}
              onChange={(e) => setFenInput(e.target.value)}
              placeholder="Paste a FEN to link a position to the chat…"
              onKeyDown={(e) => e.key === "Enter" && applyFen()}
            />
            <button className="btn btn--outline" onClick={applyFen}>
              Set
            </button>
            {fen && (
              <button
                className="btn btn--outline"
                onClick={() => {
                  setFen("");
                  setFenInput("");
                }}
              >
                Clear
              </button>
            )}
          </div>
        </div>

        <div className="chat-page__chat-col">
          <CoachChat
            userId={user.id}
            fen={fen || null}
            conversationId={convId}
            onConversationId={setConvId}
            className="chat-page__chat"
          />
        </div>
      </div>
    </div>
  );
}
