import { useState, useRef, useCallback, useEffect } from "react";
import Chessboard from "../components/Chessboard";
import CoachChat from "../components/CoachChat";
import { connectPracticeWS } from "../api/client";
import { useUser } from "../hooks/useUser";
import type { GameOverPayload, CriticalMoment } from "../types";
import type { Key } from "chessground/types";
import { Chess } from "chess.js";

type Phase = "setup" | "playing" | "finished";

export default function Practice() {
  const { user } = useUser();
  const [phase, setPhase] = useState<Phase>("setup");

  // Setup form
  const [opponent, setOpponent] = useState("");
  const [color, setColor] = useState<"white" | "black">("white");
  const [openingMoves, setOpeningMoves] = useState("");

  // Game state
  const [fen, setFen] = useState("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1");
  const [lastMove, setLastMove] = useState<[Key, Key] | undefined>();
  const [status, setStatus] = useState("");
  const [moveList, setMoveList] = useState<string[]>([]);
  const wsRef = useRef<WebSocket | null>(null);
  const chessRef = useRef(new Chess());

  // Post-game
  const [result, setResult] = useState<GameOverPayload | null>(null);

  const startGame = () => {
    if (!opponent.trim()) return;
    setPhase("playing");
    setResult(null);
    setMoveList([]);
    setStatus(`Playing vs ${opponent} (you are ${color})…`);

    const chess = new Chess();
    if (openingMoves.trim()) {
      for (const uci of openingMoves.split(",").map((s) => s.trim())) {
        if (uci) {
          const from = uci.slice(0, 2);
          const to = uci.slice(2, 4);
          const promo = uci[4] || undefined;
          chess.move({ from, to, promotion: promo });
        }
      }
    }
    chessRef.current = chess;
    setFen(chess.fen());

    const ws = connectPracticeWS({
      user_id: user.id,
      opponent,
      user_color: color,
      opening_moves: openingMoves || undefined,
    });
    wsRef.current = ws;

    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === "move") {
        // Engine move
        const from = msg.uci.slice(0, 2);
        const to = msg.uci.slice(2, 4);
        const promo = msg.uci[4] || undefined;
        chess.move({ from, to, promotion: promo });
        setFen(chess.fen());
        setLastMove([from as Key, to as Key]);
        setMoveList((ml) => [...ml, msg.uci]);
      } else if (msg.type === "game_over") {
        setPhase("finished");
        setResult(msg as GameOverPayload);
        setStatus(`Game over: ${msg.result}`);
        ws.close();
      } else if (msg.type === "error") {
        setStatus(`Error: ${msg.message}`);
      }
    };

    ws.onerror = () => setStatus("WebSocket error");
    ws.onclose = () => {
      if (phase === "playing") setStatus("Disconnected");
    };
  };

  const handleMove = useCallback(
    (from: string, to: string, promotion?: string) => {
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      const uci = from + to + (promotion || "");
      ws.send(JSON.stringify({ type: "move", uci }));
      setMoveList((ml) => [...ml, uci]);
    },
    []
  );

  const resign = () => {
    wsRef.current?.send(JSON.stringify({ type: "resign" }));
  };

  useEffect(() => {
    return () => {
      wsRef.current?.close();
    };
  }, []);

  // Formatted move list
  const formattedMoves = moveList
    .map((m, i) => (i % 2 === 0 ? `${Math.floor(i / 2) + 1}. ${m}` : m))
    .join(" ");

  return (
    <div className="page practice">
      {phase === "setup" && (
        <div className="practice__setup card">
          <h1>Practice Game</h1>
          <p className="text-muted">
            Play against a maia-individual model fine-tuned on a specific
            opponent's style.
          </p>
          <div className="form-stack">
            <label>
              Opponent (Chess.com username)
              <input
                value={opponent}
                onChange={(e) => setOpponent(e.target.value)}
                placeholder="e.g. hikaru"
              />
            </label>
            <label>
              Your color
              <select
                value={color}
                onChange={(e) => setColor(e.target.value as "white" | "black")}
              >
                <option value="white">White</option>
                <option value="black">Black</option>
              </select>
            </label>
            <label>
              Opening moves (optional, comma-separated UCI)
              <input
                value={openingMoves}
                onChange={(e) => setOpeningMoves(e.target.value)}
                placeholder="e.g. e2e4,e7e5,g1f3,b8c6"
              />
            </label>
            <button className="btn btn--primary" onClick={startGame}>
              Start Game
            </button>
          </div>
        </div>
      )}

      {(phase === "playing" || phase === "finished") && (
        <div className="practice__game">
          <div className="practice__board-col">
            <Chessboard
              fen={fen}
              orientation={color}
              interactive={phase === "playing"}
              onMove={handleMove}
              lastMove={lastMove}
              viewOnly={phase === "finished"}
            />
            <div className="practice__status">{status}</div>
            <div className="practice__moves">{formattedMoves}</div>
            {phase === "playing" && (
              <div className="btn-row">
                <button className="btn btn--danger" onClick={resign}>
                  Resign
                </button>
                <button
                  className="btn btn--outline"
                  onClick={() => {
                    wsRef.current?.close();
                    setPhase("setup");
                  }}
                >
                  Abort
                </button>
              </div>
            )}
            {phase === "finished" && (
              <button
                className="btn btn--primary"
                onClick={() => setPhase("setup")}
              >
                New Game
              </button>
            )}
          </div>

          {/* Coach chat — live during the game! */}
          <div className="practice__side">
            <CoachChat
              userId={user.id}
              fen={fen}
              placeholder={
                phase === "playing"
                  ? "Ask mid-game: 'What's the plan here?'"
                  : "Ask about the game…"
              }
            />

            {/* Post-game debrief */}
            {result && (
              <div className="practice__debrief card">
                <h3>Debrief</h3>
                <div className="practice__debrief-stats">
                  <span>Result: <strong>{result.result}</strong></span>
                  <span>Accuracy: <strong>{result.accuracy.toFixed(1)}%</strong></span>
                </div>
                {result.critical_moments.length > 0 && (
                  <div>
                    <h4>Critical Moments</h4>
                    <ul className="practice__critical">
                      {result.critical_moments.map((cm, i) => (
                        <li key={i}>
                          Move {cm.move_number}: played {cm.played}, best was{" "}
                          {cm.best ?? "?"} (swing: {cm.eval_swing} cp)
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                <div className="practice__debrief-text">
                  {result.debrief.split("\n").map((line, i) => (
                    <p key={i}>{line}</p>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
