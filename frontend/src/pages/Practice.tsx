import { useState, useRef, useCallback, useEffect } from "react";
import Chessboard from "../components/Chessboard";
import EvalBar from "../components/EvalBar";
import CoachChat from "../components/CoachChat";
import { connectPracticeWS, getRecommendedOpponents } from "../api/client";
import { useUser } from "../hooks/useUser";
import { useCoachPageContext } from "../context/CoachContext";
import type {
  GameOverPayload,
  CriticalMoment,
  MistakeEvent,
  HintResponse,
  EvalEvent,
  RecommendedOpponent,
} from "../types";
import type { Key } from "chessground/types";
import { Chess } from "chess.js";

type Phase = "setup" | "playing" | "finished";

interface Arrow {
  from: string;
  to: string;
  brush?: string;
}

export default function Practice() {
  const { user } = useUser();
  const [phase, setPhase] = useState<Phase>("setup");

  // Setup form
  const [opponent, setOpponent] = useState("");
  const [color, setColor] = useState<"white" | "black">("white");
  const [openingMoves, setOpeningMoves] = useState("");
  const [opponents, setOpponents] = useState<RecommendedOpponent[]>([]);
  const [useCustomOpp, setUseCustomOpp] = useState(false);
  const [oppLoading, setOppLoading] = useState(false);

  // Load the recommended opponents dropdown when we enter setup.
  useEffect(() => {
    if (phase !== "setup") return;
    let cancelled = false;
    setOppLoading(true);
    getRecommendedOpponents(user.id, 25)
      .then((r) => {
        if (cancelled) return;
        setOpponents(r.opponents);
        // Pre-select the top-ranked opponent if none chosen.
        if (r.opponents.length > 0 && !opponent) {
          setOpponent(r.opponents[0].opponent);
        }
      })
      .catch(() => {
        if (!cancelled) setOpponents([]);
      })
      .finally(() => {
        if (!cancelled) setOppLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, user.id]);

  // Game state
  const [fen, setFen] = useState(
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
  );
  const [lastMove, setLastMove] = useState<[Key, Key] | undefined>();
  const [status, setStatus] = useState("");
  const [moveList, setMoveList] = useState<string[]>([]);
  const wsRef = useRef<WebSocket | null>(null);
  const chessRef = useRef(new Chess());

  // Eval bar
  const [evalCp, setEvalCp] = useState<number | null>(0);
  const [evalMate, setEvalMate] = useState<number | null>(null);

  // Arrows (engine lines + hints)
  const [arrows, setArrows] = useState<Arrow[]>([]);
  const [showEngineLines, setShowEngineLines] = useState(false);

  // Mistake toast
  const [mistake, setMistake] = useState<MistakeEvent | null>(null);
  const [mistakeVisible, setMistakeVisible] = useState(false);

  // Post-game
  const [result, setResult] = useState<GameOverPayload | null>(null);

  const startGame = () => {
    if (!opponent.trim()) return;
    setPhase("playing");
    setResult(null);
    setMoveList([]);
    setArrows([]);
    setMistake(null);
    setMistakeVisible(false);
    setEvalCp(0);
    setEvalMate(null);
    setStatus(`Playing vs ${opponent} (you are ${color})...`);

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
        const from = msg.uci.slice(0, 2);
        const to = msg.uci.slice(2, 4);
        const promo = msg.uci[4] || undefined;
        chess.move({ from, to, promotion: promo });
        setFen(chess.fen());
        setLastMove([from as Key, to as Key]);
        setMoveList((ml) => [...ml, msg.uci]);
        // Clear mistake highlight on new move.
        setMistakeVisible(false);
        setArrows([]);
      } else if (msg.type === "eval") {
        const ev = msg as EvalEvent;
        setEvalCp(ev.cp);
        setEvalMate(ev.mate);
      } else if (msg.type === "mistake") {
        const m = msg as MistakeEvent;
        setMistake(m);
        setMistakeVisible(true);
        // Show the best move as a blue arrow and played move as red.
        const mistakeArrows: Arrow[] = [];
        if (m.best) {
          mistakeArrows.push({
            from: m.best.slice(0, 2),
            to: m.best.slice(2, 4),
            brush: "blue",
          });
        }
        mistakeArrows.push({
          from: m.played.slice(0, 2),
          to: m.played.slice(2, 4),
          brush: "red",
        });
        setArrows(mistakeArrows);
        // Auto-dismiss after 8 seconds.
        setTimeout(() => setMistakeVisible(false), 8000);
      } else if (msg.type === "hint") {
        const h = msg as HintResponse;
        if (h.best_uci) {
          setArrows([
            {
              from: h.best_uci.slice(0, 2),
              to: h.best_uci.slice(2, 4),
              brush: "green",
            },
          ]);
          // Clear hint arrows after 5 seconds.
          setTimeout(() => setArrows([]), 5000);
        }
      } else if (msg.type === "takeback") {
        // Reload from the new FEN.
        chess.load(msg.fen);
        setFen(msg.fen);
        setLastMove(undefined);
        setArrows([]);
        setMistakeVisible(false);
        // Remove the popped moves from our list.
        setMoveList((ml) => ml.slice(0, ml.length - (msg.popped || 0)));
        setStatus(`Took back ${msg.popped} half-move(s).`);
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
      // Clear arrows on user move.
      setArrows([]);
      setMistakeVisible(false);
    },
    []
  );

  const resign = () => {
    wsRef.current?.send(JSON.stringify({ type: "resign" }));
  };

  const requestHint = () => {
    wsRef.current?.send(JSON.stringify({ type: "hint" }));
  };

  const requestTakeback = () => {
    wsRef.current?.send(JSON.stringify({ type: "takeback" }));
  };

  useEffect(() => {
    return () => {
      wsRef.current?.close();
    };
  }, []);

  // Publish current state to the persistent coach widget so questions
  // like "why was that bad?" know which move / position the user means.
  useCoachPageContext({
    page: "practice",
    fen,
    last_move: moveList[moveList.length - 1],
    eval_cp: evalCp ?? undefined,
    opponent,
    user_color: color,
  });

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
            opponent's style. Stockfish analyses your moves in real-time.
          </p>
          <div className="form-stack">
            <label>
              Opponent
              {!useCustomOpp && opponents.length > 0 ? (
                <select
                  value={opponent}
                  onChange={(e) => setOpponent(e.target.value)}
                >
                  {opponents.map((o) => (
                    <option key={o.opponent} value={o.opponent}>
                      {o.opponent} &nbsp;—&nbsp; {o.wins}W/{o.draws}D/
                      {o.losses}L ({o.games} games)
                      {o.has_trained_model ? " ✓" : ""}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  value={opponent}
                  onChange={(e) => setOpponent(e.target.value)}
                  placeholder="e.g. hikaru"
                />
              )}
              <div className="practice__opp-controls text-muted">
                {oppLoading && "Loading opponents…"}
                {!oppLoading && opponents.length === 0 && (
                  <>
                    No opponents yet. Import your games from Progress first, or{" "}
                  </>
                )}
                {!oppLoading && (
                  <button
                    type="button"
                    className="link"
                    onClick={() => setUseCustomOpp((v) => !v)}
                  >
                    {useCustomOpp ? "use dropdown" : "enter a custom username"}
                  </button>
                )}
              </div>
            </label>
            <label>
              Your color
              <select
                value={color}
                onChange={(e) =>
                  setColor(e.target.value as "white" | "black")
                }
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
            <div className="practice__board-with-eval">
              <EvalBar
                cp={evalCp}
                mate={evalMate}
                orientation={color}
                height={480}
              />
              <Chessboard
                fen={fen}
                orientation={color}
                interactive={phase === "playing"}
                onMove={handleMove}
                lastMove={lastMove}
                viewOnly={phase === "finished"}
                arrows={arrows}
              />
            </div>
            <div className="practice__status">{status}</div>

            {/* Mistake toast */}
            {mistakeVisible && mistake && (
              <div className="mistake-toast">
                <div className="mistake-toast__header">
                  Inaccuracy ({mistake.swing_cp} cp)
                </div>
                <div className="mistake-toast__body">
                  {mistake.explanation}
                </div>
              </div>
            )}

            <div className="practice__moves">{formattedMoves}</div>

            {phase === "playing" && (
              <div className="btn-row">
                <button
                  className="btn btn--secondary practice__hint-btn"
                  onClick={requestHint}
                  title="Show best move as arrow"
                >
                  Hint
                </button>
                <button
                  className="btn btn--outline practice__takeback-btn"
                  onClick={requestTakeback}
                  title="Take back last move pair"
                >
                  Take Back
                </button>
                <label className="practice__engine-toggle">
                  <input
                    type="checkbox"
                    checked={showEngineLines}
                    onChange={(e) => setShowEngineLines(e.target.checked)}
                  />
                  Engine lines
                </label>
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
                  : "Ask about the game..."
              }
            />

            {/* Post-game debrief */}
            {result && (
              <div className="practice__debrief card">
                <h3>Debrief</h3>
                <div className="practice__debrief-stats">
                  <span>
                    Result: <strong>{result.result}</strong>
                  </span>
                  <span>
                    Accuracy: <strong>{result.accuracy.toFixed(1)}%</strong>
                  </span>
                </div>
                {result.critical_moments.length > 0 && (
                  <div>
                    <h4>Critical Moments</h4>
                    <ul className="practice__critical">
                      {result.critical_moments.map(
                        (cm: CriticalMoment, i: number) => (
                          <li key={i}>
                            Move {cm.move_number}: played {cm.played}, best was{" "}
                            {cm.best ?? "?"} (swing: {cm.eval_swing} cp)
                          </li>
                        )
                      )}
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
