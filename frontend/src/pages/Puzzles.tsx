import { useState, useEffect, useRef, useCallback } from "react";
import Chessboard from "../components/Chessboard";
import CoachChat from "../components/CoachChat";
import { nextPuzzle, submitAttempt } from "../api/client";
import { useUser } from "../hooks/useUser";
import type { Puzzle, PuzzleAttemptResult } from "../types";
import type { Key } from "chessground/types";
import { Chess } from "chess.js";

type PuzzlePhase = "loading" | "solving" | "correct" | "wrong" | "empty";

export default function Puzzles() {
  const { user, setUser } = useUser();
  const [puzzle, setPuzzle] = useState<Puzzle | null>(null);
  const [phase, setPhase] = useState<PuzzlePhase>("loading");
  const [fen, setFen] = useState("");
  const [lastMove, setLastMove] = useState<[Key, Key] | undefined>();
  const [solutionIdx, setSolutionIdx] = useState(0);
  const [showHint, setShowHint] = useState(false);
  const [showExplanation, setShowExplanation] = useState(false);
  const [rating, setRating] = useState(user.puzzle_rating);
  const [startTime, setStartTime] = useState(0);
  const [movesPlayed, setMovesPlayed] = useState<string[]>([]);
  const chessRef = useRef(new Chess());

  const loadPuzzle = useCallback(async () => {
    setPhase("loading");
    setShowHint(false);
    setShowExplanation(false);
    setSolutionIdx(0);
    setMovesPlayed([]);
    try {
      const p = await nextPuzzle(user.id);
      setPuzzle(p);
      const chess = new Chess(p.fen);
      chessRef.current = chess;

      // If there's a setup move (opponent's last move), show it as lastMove
      if (p.setup_move_uci) {
        const from = p.setup_move_uci.slice(0, 2) as Key;
        const to = p.setup_move_uci.slice(2, 4) as Key;
        setLastMove([from, to]);
      } else {
        setLastMove(undefined);
      }

      setFen(p.fen);
      setPhase("solving");
      setStartTime(Date.now());
    } catch {
      setPhase("empty");
    }
  }, [user.id]);

  useEffect(() => {
    loadPuzzle();
  }, [loadPuzzle]);

  const submit = async (solved: boolean) => {
    if (!puzzle) return;
    try {
      const res = await submitAttempt(puzzle.id, {
        user_id: user.id,
        solved,
        moves_played: movesPlayed,
        time_spent_ms: Date.now() - startTime,
        hint_used: showHint,
      });
      setRating(res.user_rating);
      setUser({ ...user, puzzle_rating: res.user_rating });
    } catch {}
  };

  const handleMove = useCallback(
    (from: string, to: string, promotion?: string) => {
      if (!puzzle || phase !== "solving") return;
      const uci = from + to + (promotion || "");
      setMovesPlayed((mp) => [...mp, uci]);

      const expected = puzzle.solution_uci[solutionIdx];
      if (uci === expected) {
        // Correct move
        const nextIdx = solutionIdx + 1;
        if (nextIdx >= puzzle.solution_uci.length) {
          // Puzzle complete!
          setPhase("correct");
          submit(true);
          return;
        }
        // Play the opponent's response automatically
        const opponentMove = puzzle.solution_uci[nextIdx];
        const chess = chessRef.current;
        const oFrom = opponentMove.slice(0, 2);
        const oTo = opponentMove.slice(2, 4);
        const oPromo = opponentMove[4] || undefined;
        chess.move({ from: oFrom, to: oTo, promotion: oPromo });
        setFen(chess.fen());
        setLastMove([oFrom as Key, oTo as Key]);
        setSolutionIdx(nextIdx + 1);
      } else {
        // Wrong
        setPhase("wrong");
        submit(false);
      }
    },
    [puzzle, phase, solutionIdx]
  );

  // Determine whose turn it is to figure out board orientation
  const orientation = puzzle
    ? puzzle.fen.includes(" w ") ? "white" : "black"
    : "white";

  return (
    <div className="page puzzles">
      <div className="puzzles__header">
        <h1>Puzzles</h1>
        <div className="puzzles__rating">
          Rating: <strong>{rating.toFixed(0)}</strong>
          {puzzle && (
            <span className="text-muted"> | Puzzle: {puzzle.rating.toFixed(0)}</span>
          )}
        </div>
      </div>

      <div className="puzzles__main">
        <div className="puzzles__board-col">
          {phase === "loading" && <div className="puzzles__loading">Loading puzzle…</div>}
          {phase === "empty" && (
            <div className="puzzles__empty card">
              <p>No puzzles available. Extract some from your games first.</p>
            </div>
          )}
          {puzzle && phase !== "loading" && phase !== "empty" && (
            <>
              <Chessboard
                fen={fen}
                orientation={orientation}
                interactive={phase === "solving"}
                onMove={handleMove}
                lastMove={lastMove}
                viewOnly={phase !== "solving"}
              />

              <div className="puzzles__feedback">
                {phase === "solving" && (
                  <p className="puzzles__prompt">
                    Find the best move for{" "}
                    {orientation === "white" ? "White" : "Black"}.
                    {puzzle.themes.length > 0 && (
                      <span className="text-muted">
                        {" "}
                        Themes: {puzzle.themes.join(", ")}
                      </span>
                    )}
                  </p>
                )}
                {phase === "correct" && (
                  <div className="puzzles__result puzzles__result--correct">
                    Correct!
                  </div>
                )}
                {phase === "wrong" && (
                  <div className="puzzles__result puzzles__result--wrong">
                    Incorrect. Best was: {puzzle.solution_san.join(" ")}
                  </div>
                )}
              </div>

              <div className="btn-row">
                {phase === "solving" && !showHint && puzzle.hint_text && (
                  <button
                    className="btn btn--outline"
                    onClick={() => setShowHint(true)}
                  >
                    Show Hint
                  </button>
                )}
                {(phase === "correct" || phase === "wrong") && (
                  <>
                    <button
                      className="btn btn--outline"
                      onClick={() => setShowExplanation(!showExplanation)}
                    >
                      {showExplanation ? "Hide" : "Show"} Explanation
                    </button>
                    <button className="btn btn--primary" onClick={loadPuzzle}>
                      Next Puzzle
                    </button>
                  </>
                )}
              </div>

              {showHint && puzzle.hint_text && (
                <div className="puzzles__hint card">{puzzle.hint_text}</div>
              )}
              {showExplanation && puzzle.explanation && (
                <div className="puzzles__explanation card">
                  {puzzle.explanation}
                </div>
              )}
            </>
          )}
        </div>

        <div className="puzzles__chat-col">
          <CoachChat
            userId={user.id}
            fen={fen || undefined}
            placeholder="Stuck? Ask the coach for a nudge…"
          />
        </div>
      </div>
    </div>
  );
}
