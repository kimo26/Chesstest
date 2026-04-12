import { useState, useEffect, useCallback, useRef } from "react";
import Chessboard from "../components/Chessboard";
import { getDueCards, reviewCard } from "../api/client";
import { useUser } from "../hooks/useUser";
import type { Flashcard, ReviewResult } from "../types";

const RATING_LABELS = ["", "Again", "Hard", "Good", "Easy"] as const;
const RATING_COLORS = ["", "#e74c3c", "#e67e22", "#27ae60", "#2980b9"];

export default function Flashcards() {
  const { user } = useUser();
  const [cards, setCards] = useState<Flashcard[]>([]);
  const [idx, setIdx] = useState(0);
  const [flipped, setFlipped] = useState(false);
  const [sessionStats, setSessionStats] = useState({ total: 0, correct: 0 });
  const [loading, setLoading] = useState(true);
  const startTimeRef = useRef(Date.now());

  const loadDue = useCallback(async () => {
    setLoading(true);
    try {
      const r = await getDueCards(user.id, 50);
      setCards(r.cards);
      setIdx(0);
      setFlipped(false);
    } catch {
      setCards([]);
    } finally {
      setLoading(false);
    }
  }, [user.id]);

  useEffect(() => {
    loadDue();
  }, [loadDue]);

  const card = cards[idx] ?? null;
  const remaining = cards.length - idx;

  const handleRating = async (rating: number) => {
    if (!card) return;
    const duration = Date.now() - startTimeRef.current;
    try {
      await reviewCard(card.id, {
        user_id: user.id,
        rating,
        review_duration_ms: duration,
      });
    } catch {}
    setSessionStats((s) => ({
      total: s.total + 1,
      correct: s.correct + (rating >= 3 ? 1 : 0),
    }));
    setFlipped(false);
    startTimeRef.current = Date.now();
    if (idx + 1 < cards.length) {
      setIdx(idx + 1);
    } else {
      // Reload to see if more are due now (learning-step cards become due fast)
      loadDue();
    }
  };

  if (loading) {
    return (
      <div className="page flashcards">
        <h1>Flashcards</h1>
        <p>Loading…</p>
      </div>
    );
  }

  if (cards.length === 0) {
    return (
      <div className="page flashcards">
        <h1>Flashcards</h1>
        <div className="card flashcards__empty">
          <p>No cards due right now. Go explore an opening and generate some!</p>
        </div>
      </div>
    );
  }

  return (
    <div className="page flashcards">
      <div className="flashcards__header">
        <h1>Flashcards</h1>
        <div className="flashcards__meta">
          <span>{remaining} remaining</span>
          <span className="text-muted">
            Session: {sessionStats.total} reviewed,{" "}
            {sessionStats.total > 0
              ? ((sessionStats.correct / sessionStats.total) * 100).toFixed(0)
              : 0}
            % recalled
          </span>
        </div>
      </div>

      {card && (
        <div className="flashcards__card-area">
          {card.fen && (
            <div className="flashcards__board">
              <Chessboard fen={card.fen} viewOnly width={320} height={320} />
            </div>
          )}

          <div className={`flashcards__card ${flipped ? "flashcards__card--flipped" : ""}`}>
            <div className="flashcards__card-type">
              <span className="badge">{card.card_type}</span>
              {card.tags.map((t) => (
                <span key={t} className="badge badge--info">
                  {t}
                </span>
              ))}
            </div>

            <div className="flashcards__front">
              <h3>Question</h3>
              <p>{card.front_text}</p>
              {card.hint && !flipped && (
                <details className="flashcards__hint">
                  <summary>Hint</summary>
                  <p>{card.hint}</p>
                </details>
              )}
            </div>

            {flipped && (
              <div className="flashcards__back">
                <h3>Answer</h3>
                <p>{card.back_text}</p>
              </div>
            )}
          </div>

          {!flipped ? (
            <button
              className="btn btn--primary flashcards__flip-btn"
              onClick={() => setFlipped(true)}
            >
              Show Answer
            </button>
          ) : (
            <div className="flashcards__ratings">
              <p>How well did you recall?</p>
              <div className="flashcards__rating-btns">
                {[1, 2, 3, 4].map((r) => (
                  <button
                    key={r}
                    className="btn flashcards__rating-btn"
                    style={{ borderColor: RATING_COLORS[r], color: RATING_COLORS[r] }}
                    onClick={() => handleRating(r)}
                  >
                    {RATING_LABELS[r]}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
