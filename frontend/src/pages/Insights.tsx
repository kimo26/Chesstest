import { useEffect, useState, useCallback } from "react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  BarChart,
  Bar,
  Cell,
  PieChart,
  Pie,
} from "recharts";
import Chessboard from "../components/Chessboard";
import EvalBar from "../components/EvalBar";
import {
  getInsights,
  analyseAllGames,
  getGameAnalysis,
  getCoachingSummary,
} from "../api/client";
import { useUser } from "../hooks/useUser";
import { useCoachPageContext } from "../context/CoachContext";
import type {
  GameInsights,
  GameAnalysis,
  GameSummary,
  MoveAnalysis,
} from "../types";

export default function Insights() {
  const { user } = useUser();
  const [insights, setInsights] = useState<GameInsights | null>(null);
  const [loading, setLoading] = useState(true);
  const [analyseMsg, setAnalyseMsg] = useState("");
  const [coachingSummary, setCoachingSummary] = useState<string | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);

  // Selected game drill-down
  const [selectedGame, setSelectedGame] = useState<GameAnalysis | null>(null);
  const [selectedMoveIdx, setSelectedMoveIdx] = useState(0);
  const [gameLoading, setGameLoading] = useState(false);

  useCoachPageContext({
    page: "insights",
    viewing_game_id: selectedGame?.game_id ?? null,
    avg_accuracy: insights?.avg_accuracy,
  });

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getInsights(user.id);
      setInsights(data);
    } catch {
      setInsights(null);
    }
    setLoading(false);
  }, [user.id]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const doAnalyseAll = async () => {
    setAnalyseMsg("Starting batch analysis...");
    try {
      await analyseAllGames(user.id);
      setAnalyseMsg(
        "Analysis started in background. Refresh in a few minutes."
      );
    } catch (err) {
      setAnalyseMsg(`Error: ${err}`);
    }
  };

  const loadGame = async (gameId: number) => {
    setGameLoading(true);
    setSelectedMoveIdx(0);
    try {
      const data = await getGameAnalysis(user.id, gameId);
      setSelectedGame(data);
    } catch {
      setSelectedGame(null);
    }
    setGameLoading(false);
  };

  const doCoachingSummary = async () => {
    setSummaryLoading(true);
    try {
      const r = await getCoachingSummary(user.id);
      setCoachingSummary(r.summary);
    } catch {
      setCoachingSummary("Failed to generate coaching summary.");
    }
    setSummaryLoading(false);
  };

  if (loading) {
    return (
      <div className="page insights">
        <h1>Game Insights</h1>
        <p>Loading...</p>
      </div>
    );
  }

  if (!insights || insights.total_games === 0) {
    return (
      <div className="page insights">
        <h1>Game Insights</h1>
        <div className="card">
          <p>
            No analysed games yet. Import your games from the Progress page,
            then click below to analyse them.
          </p>
          <button className="btn btn--primary" onClick={doAnalyseAll}>
            Analyse All Games
          </button>
          {analyseMsg && <p className="text-muted">{analyseMsg}</p>}
        </div>
      </div>
    );
  }

  const winRate =
    insights.total_games > 0
      ? ((insights.wins / insights.total_games) * 100).toFixed(0)
      : "0";

  const pieData = [
    { name: "Wins", value: insights.wins, fill: "#27ae60" },
    { name: "Draws", value: insights.draws, fill: "#95a5a6" },
    { name: "Losses", value: insights.losses, fill: "#e74c3c" },
  ];

  const phaseData = [
    { phase: "Opening", accuracy: +insights.phases.opening.toFixed(1) },
    { phase: "Middlegame", accuracy: +insights.phases.middlegame.toFixed(1) },
    { phase: "Endgame", accuracy: +insights.phases.endgame.toFixed(1) },
  ];

  // The currently selected move for drill-down
  const currentMove: MoveAnalysis | undefined =
    selectedGame?.moves[selectedMoveIdx];

  // Build a FEN to show at the selected move. We reconstruct from the first
  // position by replaying moves up to the selectedMoveIdx. But since we only
  // have UCI data, we just show the cp graph and the move info.
  const moveAccuracyData =
    selectedGame?.moves
      .filter((m) => m.is_user_move)
      .map((m) => ({
        move: m.move_number,
        accuracy: +m.accuracy.toFixed(0),
        cp: m.cp_after,
      })) ?? [];

  return (
    <div className="page insights">
      <div className="insights__header">
        <h1>Game Insights</h1>
        <div className="btn-row">
          <button className="btn btn--primary" onClick={doAnalyseAll}>
            Analyse All Games
          </button>
          <button
            className="btn btn--secondary"
            onClick={doCoachingSummary}
            disabled={summaryLoading}
          >
            {summaryLoading ? "Generating..." : "Get Coaching Summary"}
          </button>
          <button className="btn btn--outline" onClick={refresh}>
            Refresh
          </button>
        </div>
        {analyseMsg && <p className="text-muted">{analyseMsg}</p>}
      </div>

      {/* Coaching summary */}
      {coachingSummary && (
        <div className="card insights__coaching-summary">
          <h2>Coaching Summary</h2>
          {coachingSummary.split("\n").map((line, i) => (
            <p key={i}>{line}</p>
          ))}
        </div>
      )}

      <div className="insights__grid">
        {/* Overall stats */}
        <div className="card insights__stat-card">
          <h3>Games Analysed</h3>
          <div className="insights__big-number">{insights.total_games}</div>
        </div>
        <div className="card insights__stat-card">
          <h3>Average Accuracy</h3>
          <div className="insights__big-number">
            {insights.avg_accuracy.toFixed(1)}%
          </div>
        </div>
        <div className="card insights__stat-card">
          <h3>Win Rate</h3>
          <div className="insights__big-number">{winRate}%</div>
          <div className="text-muted">
            {insights.wins}W / {insights.draws}D / {insights.losses}L
          </div>
        </div>
        <div className="card insights__stat-card">
          <h3>Mistakes</h3>
          <div className="insights__mistake-counts">
            <span className="insights__blunder">
              {insights.mistake_counts.blunders} blunders
            </span>
            <span className="insights__mistake">
              {insights.mistake_counts.mistakes} mistakes
            </span>
            <span className="insights__inaccuracy">
              {insights.mistake_counts.inaccuracies} inaccuracies
            </span>
          </div>
        </div>

        {/* Win/Draw/Loss pie */}
        <section className="card insights__chart-card">
          <h2>Results</h2>
          <ResponsiveContainer width="100%" height={200}>
            <PieChart>
              <Pie
                data={pieData}
                dataKey="value"
                nameKey="name"
                cx="50%"
                cy="50%"
                outerRadius={80}
                label={({ name, value }) => `${name}: ${value}`}
              >
                {pieData.map((entry, i) => (
                  <Cell key={i} fill={entry.fill} />
                ))}
              </Pie>
              <Tooltip />
            </PieChart>
          </ResponsiveContainer>
        </section>

        {/* Accuracy over time */}
        <section className="card insights__chart-card insights__chart-wide">
          <h2>Accuracy Over Time</h2>
          <ResponsiveContainer width="100%" height={250}>
            <LineChart data={insights.accuracy_over_time}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="date" tick={{ fontSize: 10 }} />
              <YAxis domain={[0, 100]} />
              <Tooltip />
              <Line
                type="monotone"
                dataKey="accuracy"
                stroke="#2980b9"
                strokeWidth={2}
                dot={{ r: 3 }}
              />
            </LineChart>
          </ResponsiveContainer>
        </section>

        {/* Phase performance */}
        <section className="card insights__chart-card">
          <h2>Phase Performance</h2>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={phaseData}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="phase" />
              <YAxis domain={[0, 100]} />
              <Tooltip />
              <Bar dataKey="accuracy" radius={[4, 4, 0, 0]}>
                {phaseData.map((_, i) => (
                  <Cell
                    key={i}
                    fill={
                      i === 0
                        ? "#3498db"
                        : i === 1
                        ? "#e67e22"
                        : "#9b59b6"
                    }
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </section>

        {/* Opening performance table */}
        <section className="card insights__chart-card insights__chart-wide">
          <h2>Opening Performance</h2>
          {insights.opening_performance.length > 0 ? (
            <table className="table">
              <thead>
                <tr>
                  <th>ECO</th>
                  <th>Opening</th>
                  <th>Games</th>
                  <th>Win %</th>
                  <th>Avg Accuracy</th>
                </tr>
              </thead>
              <tbody>
                {insights.opening_performance.map((o, i) => (
                  <tr key={i}>
                    <td>
                      <span className="badge">{o.eco_code}</span>
                    </td>
                    <td>{o.opening_name}</td>
                    <td>{o.games}</td>
                    <td>{(o.win_rate * 100).toFixed(0)}%</td>
                    <td>{o.avg_accuracy.toFixed(1)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="text-muted">No opening data available.</p>
          )}
        </section>

        {/* Game list */}
        <section className="card insights__chart-card insights__chart-wide">
          <h2>Game History</h2>
          <div className="insights__game-list">
            {insights.games.map((g: GameSummary) => (
              <div
                key={g.game_id}
                className={`insights__game-item ${
                  selectedGame?.game_id === g.game_id
                    ? "insights__game-item--selected"
                    : ""
                }`}
                onClick={() => loadGame(g.game_id)}
              >
                <span className="insights__game-date">{g.date}</span>
                <span className="insights__game-opponent">
                  vs {g.opponent || "?"}
                </span>
                <span
                  className={`insights__game-result insights__game-result--${
                    g.result === "win" || g.result === "1-0" || g.result === "0-1"
                      ? "win"
                      : g.result === "draw"
                      ? "draw"
                      : "loss"
                  }`}
                >
                  {g.result}
                </span>
                <span className="insights__game-accuracy">
                  {g.accuracy.toFixed(0)}%
                </span>
                <span className="text-muted">
                  {g.eco_code} {g.opening_name?.slice(0, 25)}
                </span>
              </div>
            ))}
          </div>
        </section>

        {/* Game drill-down */}
        {gameLoading && (
          <section className="card insights__chart-card insights__chart-wide">
            <p>Loading game analysis...</p>
          </section>
        )}
        {selectedGame && !gameLoading && (
          <section className="card insights__chart-card insights__chart-wide">
            <h2>
              Game Analysis: vs {selectedGame.opponent_name || "?"} (
              {selectedGame.played_at.slice(0, 10)})
            </h2>
            <div className="insights__game-detail">
              <div className="insights__game-stats">
                <span>
                  Result: <strong>{selectedGame.result}</strong>
                </span>
                <span>
                  Accuracy:{" "}
                  <strong>{selectedGame.avg_accuracy.toFixed(1)}%</strong>
                </span>
                <span className="insights__blunder">
                  {selectedGame.blunders} blunders
                </span>
                <span className="insights__mistake">
                  {selectedGame.mistakes} mistakes
                </span>
                <span className="insights__inaccuracy">
                  {selectedGame.inaccuracies} inaccuracies
                </span>
              </div>

              {/* Move accuracy chart */}
              {moveAccuracyData.length > 0 && (
                <ResponsiveContainer width="100%" height={180}>
                  <LineChart data={moveAccuracyData}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="move" />
                    <YAxis domain={[0, 100]} />
                    <Tooltip />
                    <Line
                      type="monotone"
                      dataKey="accuracy"
                      stroke="#2980b9"
                      strokeWidth={2}
                      dot={{ r: 2 }}
                    />
                  </LineChart>
                </ResponsiveContainer>
              )}

              {/* Move-by-move table */}
              <div className="insights__move-table">
                <table className="table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Move</th>
                      <th>Eval</th>
                      <th>Accuracy</th>
                      <th>Best</th>
                    </tr>
                  </thead>
                  <tbody>
                    {selectedGame.moves
                      .filter((m) => m.is_user_move)
                      .map((m, i) => (
                        <tr
                          key={i}
                          className={
                            m.swing >= 300
                              ? "insights__row--blunder"
                              : m.swing >= 100
                              ? "insights__row--mistake"
                              : m.swing >= 50
                              ? "insights__row--inaccuracy"
                              : ""
                          }
                        >
                          <td>{m.move_number}</td>
                          <td>{m.move_uci}</td>
                          <td>
                            {m.cp_after > 0 ? "+" : ""}
                            {(m.cp_after / 100).toFixed(1)}
                          </td>
                          <td>{m.accuracy.toFixed(0)}%</td>
                          <td className="text-muted">{m.best_uci || "—"}</td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
