import { useState, useEffect, useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import Chessboard from "../components/Chessboard";
import EvalBar from "../components/EvalBar";
import CoachChat from "../components/CoachChat";
import WinBar from "../components/WinBar";
import { getOpening, searchOpenings, generateCards, analyse } from "../api/client";
import { useUser } from "../hooks/useUser";
import { useCoachPageContext } from "../context/CoachContext";
import type { OpeningNode, AnalysisLine } from "../types";

interface Arrow {
  from: string;
  to: string;
  brush?: string;
}

const LINE_BRUSHES = ["green", "blue", "yellow"];

export default function OpeningsExplorer() {
  const { user } = useUser();
  const [params, setParams] = useSearchParams();
  const [node, setNode] = useState<OpeningNode | null>(null);
  const [search, setSearch] = useState("");
  const [results, setResults] = useState<OpeningNode[]>([]);
  const [cardsMsg, setCardsMsg] = useState("");

  // Engine analysis state
  const [engineLines, setEngineLines] = useState<AnalysisLine[]>([]);
  const [analysing, setAnalysing] = useState(false);
  const [showArrows, setShowArrows] = useState(true);

  // Publish the current opening node + FEN to the widget.
  useCoachPageContext({
    page: "openings",
    fen: node?.fen,
    opening_name: node?.opening_name ?? null,
    eco_code: node?.eco_code ?? null,
    opening_node_id: node?.id ?? null,
  });

  const nodeId = params.get("id") ? Number(params.get("id")) : null;

  const load = useCallback(async (id: number) => {
    try {
      const data = await getOpening(id);
      setNode(data);
      setEngineLines([]);
    } catch {
      setNode(null);
    }
  }, []);

  useEffect(() => {
    if (nodeId) load(nodeId);
  }, [nodeId, load]);

  const doSearch = async () => {
    if (!search.trim()) return;
    try {
      const r = await searchOpenings(search);
      setResults(r.results);
    } catch {
      setResults([]);
    }
  };

  const navigate = (id: number) => {
    setParams({ id: String(id) });
    setResults([]);
    setSearch("");
  };

  const doGenCards = async () => {
    if (!node) return;
    setCardsMsg("Generating...");
    try {
      const r = await generateCards(user.id, node.id, 6);
      setCardsMsg(`Created ${r.created.length} flashcards`);
    } catch (err) {
      setCardsMsg(`Error: ${err}`);
    }
  };

  const doAnalyse = async () => {
    if (!node) return;
    setAnalysing(true);
    try {
      const r = await analyse({ fen: node.fen, depth: 22, multipv: 3 });
      setEngineLines(r.lines);
    } catch {
      setEngineLines([]);
    }
    setAnalysing(false);
  };

  // Build arrows from engine lines.
  const engineArrows: Arrow[] =
    showArrows && engineLines.length > 0
      ? engineLines
          .filter((l) => l.pv.length > 0)
          .map((l, i) => ({
            from: l.pv[0].slice(0, 2),
            to: l.pv[0].slice(2, 4),
            brush: LINE_BRUSHES[i % LINE_BRUSHES.length],
          }))
      : [];

  // Eval from top line.
  const topLine = engineLines[0];
  const evalCp = topLine?.cp ?? null;
  const evalMate = topLine?.mate ?? null;

  return (
    <div className="page openings">
      {/* Search bar */}
      <div className="openings__search">
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && doSearch()}
          placeholder="Search openings (e.g. Sicilian, Najdorf, B90)..."
        />
        <button className="btn btn--primary" onClick={doSearch}>
          Search
        </button>
      </div>

      {results.length > 0 && (
        <ul className="openings__results">
          {results.map((r) => (
            <li
              key={r.id}
              onClick={() => navigate(r.id)}
              className="openings__result-item"
            >
              <span className="badge">{r.eco_code}</span>{" "}
              {r.opening_name}{" "}
              <span className="text-muted">depth {r.depth}</span>
            </li>
          ))}
        </ul>
      )}

      {node && (
        <div className="openings__detail">
          <div className="openings__board-col">
            <div className="openings__board-with-eval">
              {engineLines.length > 0 && (
                <EvalBar
                  cp={evalCp}
                  mate={evalMate}
                  orientation="white"
                  height={480}
                />
              )}
              <Chessboard
                fen={node.fen}
                viewOnly
                orientation="white"
                arrows={engineArrows}
              />
            </div>
            <div className="openings__moves">{node.move_sequence}</div>

            {/* Engine analysis results */}
            {engineLines.length > 0 && (
              <div className="openings__engine-lines">
                <div className="openings__engine-header">
                  <strong>Engine Analysis</strong>
                  <label className="openings__arrow-toggle">
                    <input
                      type="checkbox"
                      checked={showArrows}
                      onChange={(e) => setShowArrows(e.target.checked)}
                    />
                    Show arrows
                  </label>
                </div>
                {engineLines.map((line, i) => {
                  const evalStr =
                    line.mate !== null
                      ? `M${line.mate}`
                      : line.cp !== null
                      ? `${line.cp > 0 ? "+" : ""}${(line.cp / 100).toFixed(1)}`
                      : "?";
                  return (
                    <div key={i} className="openings__engine-line">
                      <span
                        className="openings__engine-eval"
                        style={{
                          color:
                            LINE_BRUSHES[i % LINE_BRUSHES.length] === "green"
                              ? "var(--clr-success)"
                              : LINE_BRUSHES[i % LINE_BRUSHES.length] === "blue"
                              ? "var(--clr-info)"
                              : "var(--clr-warning)",
                        }}
                      >
                        {evalStr}
                      </span>
                      <span className="openings__engine-pv">
                        {line.pv.slice(0, 8).join(" ")}
                      </span>
                      <span className="text-muted">d{line.depth}</span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          <div className="openings__info-col">
            <h2>
              <span className="badge">{node.eco_code}</span> {node.opening_name}
            </h2>
            <WinBar
              white={node.white_wins}
              draws={node.draws}
              black={node.black_wins}
            />
            <p className="text-muted">
              {node.total_games.toLocaleString()} games, depth {node.depth}
            </p>

            {node.description && (
              <div className="openings__description">
                {node.description.split("\n\n").map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </div>
            )}

            {node.themes && node.themes.length > 0 && (
              <div className="openings__themes">
                {node.themes.map((t) => (
                  <span key={t} className="badge badge--info">
                    {t}
                  </span>
                ))}
              </div>
            )}

            {node.typical_plans && (
              <div className="openings__plans">
                {node.typical_plans.white && (
                  <div>
                    <strong>White plans:</strong>{" "}
                    {(node.typical_plans.white as string[]).join(", ")}
                  </div>
                )}
                {node.typical_plans.black && (
                  <div>
                    <strong>Black plans:</strong>{" "}
                    {(node.typical_plans.black as string[]).join(", ")}
                  </div>
                )}
              </div>
            )}

            {/* Children / sub-variations */}
            {node.children && node.children.length > 0 && (
              <div className="openings__children">
                <h3>Continuations</h3>
                <ul>
                  {node.children.map((c) => (
                    <li key={c.id}>
                      <button className="link" onClick={() => navigate(c.id)}>
                        {c.move_san} — {c.opening_name || "unnamed"}{" "}
                        <span className="text-muted">
                          ({(c.total_games ?? 0).toLocaleString()})
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* Parent navigation */}
            {node.parent_id && (
              <button
                className="btn btn--outline"
                onClick={() => navigate(node.parent_id!)}
              >
                &larr; Parent line
              </button>
            )}

            <div className="btn-row" style={{ marginTop: 16 }}>
              <button
                className="btn btn--primary"
                onClick={doAnalyse}
                disabled={analysing}
              >
                {analysing ? "Analysing..." : "Analyse with Stockfish"}
              </button>
              <button className="btn btn--secondary" onClick={doGenCards}>
                Generate Flashcards
              </button>
              {cardsMsg && <span className="text-muted">{cardsMsg}</span>}
            </div>
          </div>

          {/* Coach available while exploring */}
          <div className="openings__chat-col">
            <CoachChat
              userId={user.id}
              fen={node.fen}
              placeholder="Ask about this opening..."
            />
          </div>
        </div>
      )}

      {!node && results.length === 0 && (
        <div className="openings__empty">
          <p>Search for an opening or browse the tree to get started.</p>
        </div>
      )}
    </div>
  );
}
