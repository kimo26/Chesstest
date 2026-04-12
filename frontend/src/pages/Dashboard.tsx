import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getDueCards, getWeaknesses } from "../api/client";
import type { Weakness, Flashcard } from "../types";
import { useUser } from "../hooks/useUser";
import WinBar from "../components/WinBar";

export default function Dashboard() {
  const { user, setUser } = useUser();
  const [weaknesses, setWeaknesses] = useState<Weakness[]>([]);
  const [dueCount, setDueCount] = useState(0);
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({ username: user.username, chess_com_user: user.chess_com_user });

  useEffect(() => {
    if (!user.id) return;
    getWeaknesses(user.id, 5)
      .then((r) => setWeaknesses(r.weaknesses))
      .catch(() => {});
    getDueCards(user.id, 1)
      .then((r) => setDueCount(r.cards.length > 0 ? r.cards.length : 0))
      .catch(() => {});
  }, [user.id]);

  const saveProfile = () => {
    setUser({ ...user, ...form });
    setEditing(false);
  };

  return (
    <div className="page dashboard">
      <div className="dashboard__hero">
        <h1>Welcome back, {user.username}</h1>
        <div className="dashboard__stats">
          <div className="stat-card">
            <div className="stat-card__value">{user.puzzle_rating.toFixed(0)}</div>
            <div className="stat-card__label">Puzzle Rating</div>
          </div>
          <div className="stat-card">
            <div className="stat-card__value">{dueCount}</div>
            <div className="stat-card__label">Cards Due</div>
          </div>
          <div className="stat-card">
            <div className="stat-card__value">{weaknesses.length}</div>
            <div className="stat-card__label">Weak Openings</div>
          </div>
        </div>
      </div>

      <div className="dashboard__grid">
        {/* Quick actions */}
        <section className="card">
          <h2>Quick Start</h2>
          <div className="dashboard__actions">
            <Link to="/practice" className="btn btn--primary">
              Play Practice Game
            </Link>
            <Link to="/puzzles" className="btn btn--secondary">
              Solve Puzzles
            </Link>
            <Link to="/flashcards" className="btn btn--secondary">
              Review Flashcards
            </Link>
            <Link to="/chat" className="btn btn--outline">
              Ask the Coach
            </Link>
          </div>
        </section>

        {/* Weakest openings */}
        <section className="card">
          <h2>Weakest Openings</h2>
          {weaknesses.length === 0 ? (
            <p className="text-muted">
              Import your games to discover your weak spots.
            </p>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Opening</th>
                  <th>Color</th>
                  <th>Games</th>
                  <th>Win %</th>
                  <th>Issue</th>
                </tr>
              </thead>
              <tbody>
                {weaknesses.map((w) => (
                  <tr key={`${w.opening_node_id}-${w.color}`}>
                    <td>
                      <Link to={`/openings?id=${w.opening_node_id}`}>
                        {w.opening_name || w.eco_code || "?"}
                      </Link>
                    </td>
                    <td>{w.color}</td>
                    <td>{w.games_played}</td>
                    <td>{(w.win_rate * 100).toFixed(0)}%</td>
                    <td>
                      <span className="badge badge--warning">
                        {w.weakness_type}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <Link to="/progress" className="link">
            View full analysis &rarr;
          </Link>
        </section>

        {/* Profile */}
        <section className="card">
          <h2>Profile</h2>
          {editing ? (
            <div className="form-stack">
              <label>
                Username
                <input
                  value={form.username}
                  onChange={(e) => setForm({ ...form, username: e.target.value })}
                />
              </label>
              <label>
                Chess.com handle
                <input
                  value={form.chess_com_user}
                  onChange={(e) =>
                    setForm({ ...form, chess_com_user: e.target.value })
                  }
                />
              </label>
              <div className="btn-row">
                <button className="btn btn--primary" onClick={saveProfile}>
                  Save
                </button>
                <button className="btn btn--outline" onClick={() => setEditing(false)}>
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <div>
              <p>
                <strong>Chess.com:</strong>{" "}
                {user.chess_com_user || <em>not set</em>}
              </p>
              <button className="btn btn--outline" onClick={() => setEditing(true)}>
                Edit
              </button>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
