import { useEffect, useState, useCallback } from "react";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  BarChart, Bar, Cell,
} from "recharts";
import {
  getWeaknesses,
  recomputeWeaknesses,
  importGames,
  importOpponent,
  startTraining,
  trainingStatus,
} from "../api/client";
import { useUser } from "../hooks/useUser";
import type { Weakness } from "../types";

export default function Progress() {
  const { user } = useUser();
  const [weaknesses, setWeaknesses] = useState<Weakness[]>([]);
  const [importMsg, setImportMsg] = useState("");
  const [oppInput, setOppInput] = useState("");
  const [trainMsg, setTrainMsg] = useState("");

  const refresh = useCallback(async () => {
    try {
      const r = await getWeaknesses(user.id, 20);
      setWeaknesses(r.weaknesses);
    } catch {}
  }, [user.id]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const doImport = async () => {
    if (!user.chess_com_user) {
      setImportMsg("Set your Chess.com username in the dashboard first.");
      return;
    }
    setImportMsg("Importing…");
    try {
      const r = await importGames({
        user_id: user.id,
        chess_com_username: user.chess_com_user,
        months_back: 12,
      });
      setImportMsg(`Imported ${r.inserted} games.`);
      await recomputeWeaknesses(user.id);
      refresh();
    } catch (err) {
      setImportMsg(`Error: ${err}`);
    }
  };

  const doImportOpponent = async () => {
    if (!oppInput.trim()) return;
    setTrainMsg("Importing opponent games…");
    try {
      const r = await importOpponent({ opponent: oppInput, months_back: 24 });
      setTrainMsg(`Imported ${r.inserted} opponent games.`);
    } catch (err) {
      setTrainMsg(`Error: ${err}`);
    }
  };

  const doTrain = async () => {
    if (!oppInput.trim()) return;
    setTrainMsg("Starting fine-tune…");
    try {
      const r = await startTraining({ opponent: oppInput });
      setTrainMsg(`Training: ${r.status}`);
    } catch (err) {
      setTrainMsg(`Error: ${err}`);
    }
  };

  const checkTrain = async () => {
    if (!oppInput.trim()) return;
    try {
      const r = await trainingStatus(oppInput);
      setTrainMsg(`Status: ${r.status}${r.error ? ` — ${r.error}` : ""}`);
    } catch (err) {
      setTrainMsg(`Error: ${err}`);
    }
  };

  // Chart data: weakness score per opening
  const chartData = weaknesses.map((w) => ({
    name: (w.eco_code || "") + " " + (w.opening_name || "").slice(0, 20),
    score: +(w.weakness_score * 100).toFixed(0),
    winRate: +(w.win_rate * 100).toFixed(0),
    games: w.games_played,
  }));

  // Simulated rating history (in production you'd query puzzle_attempts)
  const ratingHistory = [
    { session: "1", rating: user.puzzle_rating - 80 },
    { session: "2", rating: user.puzzle_rating - 50 },
    { session: "3", rating: user.puzzle_rating - 30 },
    { session: "4", rating: user.puzzle_rating - 10 },
    { session: "5", rating: user.puzzle_rating },
  ];

  return (
    <div className="page progress">
      <h1>Progress & Stats</h1>

      <div className="progress__grid">
        {/* Puzzle rating trend */}
        <section className="card">
          <h2>Puzzle Rating</h2>
          <p className="progress__current-rating">
            {user.puzzle_rating.toFixed(0)}
          </p>
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={ratingHistory}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="session" />
              <YAxis domain={["auto", "auto"]} />
              <Tooltip />
              <Line
                type="monotone"
                dataKey="rating"
                stroke="#2980b9"
                strokeWidth={2}
                dot={{ r: 4 }}
              />
            </LineChart>
          </ResponsiveContainer>
        </section>

        {/* Weakness chart */}
        <section className="card">
          <h2>Opening Weaknesses</h2>
          {chartData.length > 0 ? (
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={chartData} layout="vertical" margin={{ left: 100 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis type="number" domain={[0, 100]} />
                <YAxis type="category" dataKey="name" width={120} tick={{ fontSize: 11 }} />
                <Tooltip
                  formatter={(val: number, name: string) => [
                    `${val}${name === "winRate" ? "%" : ""}`,
                    name === "score" ? "Weakness" : "Win Rate",
                  ]}
                />
                <Bar dataKey="score" name="Weakness Score" radius={[0, 4, 4, 0]}>
                  {chartData.map((_, i) => (
                    <Cell key={i} fill={i < 3 ? "#e74c3c" : "#e67e22"} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-muted">No data yet. Import your games below.</p>
          )}
        </section>

        {/* Weakness table */}
        <section className="card progress__full-width">
          <h2>Detailed Weakness Breakdown</h2>
          {weaknesses.length > 0 ? (
            <table className="table">
              <thead>
                <tr>
                  <th>ECO</th>
                  <th>Opening</th>
                  <th>Color</th>
                  <th>Games</th>
                  <th>Win %</th>
                  <th>Avg Theory Depth</th>
                  <th>Score</th>
                  <th>Type</th>
                </tr>
              </thead>
              <tbody>
                {weaknesses.map((w) => (
                  <tr key={`${w.opening_node_id}-${w.color}`}>
                    <td>{w.eco_code}</td>
                    <td>{w.opening_name}</td>
                    <td>{w.color}</td>
                    <td>{w.games_played}</td>
                    <td>{(w.win_rate * 100).toFixed(0)}%</td>
                    <td>{w.avg_deviation_ply?.toFixed(1) ?? "—"}</td>
                    <td>
                      <div
                        className="progress__score-bar"
                        style={{ width: `${w.weakness_score * 100}%` }}
                      />
                      {(w.weakness_score * 100).toFixed(0)}
                    </td>
                    <td>
                      <span className="badge badge--warning">{w.weakness_type}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="text-muted">Import games to see your weakness breakdown.</p>
          )}
        </section>

        {/* Import & training controls */}
        <section className="card">
          <h2>Import Games</h2>
          <p className="text-muted">
            Pull your recent games from Chess.com and recompute weaknesses.
          </p>
          <button className="btn btn--primary" onClick={doImport}>
            Import My Games
          </button>
          {importMsg && <p>{importMsg}</p>}
        </section>

        <section className="card">
          <h2>Train Maia on Opponent</h2>
          <p className="text-muted">
            Import an opponent's games, then fine-tune a maia-individual
            model to mimic their play style.
          </p>
          <div className="form-stack">
            <input
              value={oppInput}
              onChange={(e) => setOppInput(e.target.value)}
              placeholder="Opponent Chess.com username"
            />
            <div className="btn-row">
              <button className="btn btn--secondary" onClick={doImportOpponent}>
                Import Games
              </button>
              <button className="btn btn--primary" onClick={doTrain}>
                Start Training
              </button>
              <button className="btn btn--outline" onClick={checkTrain}>
                Check Status
              </button>
            </div>
          </div>
          {trainMsg && <p>{trainMsg}</p>}
        </section>
      </div>
    </div>
  );
}
