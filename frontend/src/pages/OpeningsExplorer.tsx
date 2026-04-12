import { useState, useEffect, useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import Chessboard from "../components/Chessboard";
import CoachChat from "../components/CoachChat";
import WinBar from "../components/WinBar";
import { getOpening, searchOpenings, generateCards } from "../api/client";
import { useUser } from "../hooks/useUser";
import type { OpeningNode } from "../types";

export default function OpeningsExplorer() {
  const { user } = useUser();
  const [params, setParams] = useSearchParams();
  const [node, setNode] = useState<OpeningNode | null>(null);
  const [search, setSearch] = useState("");
  const [results, setResults] = useState<OpeningNode[]>([]);
  const [cardsMsg, setCardsMsg] = useState("");

  const nodeId = params.get("id") ? Number(params.get("id")) : null;

  const load = useCallback(async (id: number) => {
    try {
      const data = await getOpening(id);
      setNode(data);
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
    setCardsMsg("Generating…");
    try {
      const r = await generateCards(user.id, node.id, 6);
      setCardsMsg(`Created ${r.created.length} flashcards`);
    } catch (err) {
      setCardsMsg(`Error: ${err}`);
    }
  };

  return (
    <div className="page openings">
      {/* Search bar */}
      <div className="openings__search">
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && doSearch()}
          placeholder="Search openings (e.g. Sicilian, Najdorf, B90)…"
        />
        <button className="btn btn--primary" onClick={doSearch}>
          Search
        </button>
      </div>

      {results.length > 0 && (
        <ul className="openings__results">
          {results.map((r) => (
            <li key={r.id} onClick={() => navigate(r.id)} className="openings__result-item">
              <span className="badge">{r.eco_code}</span>{" "}
              {r.opening_name} <span className="text-muted">depth {r.depth}</span>
            </li>
          ))}
        </ul>
      )}

      {node && (
        <div className="openings__detail">
          <div className="openings__board-col">
            <Chessboard fen={node.fen} viewOnly orientation="white" />
            <div className="openings__moves">{node.move_sequence}</div>
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
              <button className="btn btn--secondary" onClick={doGenCards}>
                Generate Flashcards
              </button>
              {cardsMsg && <span className="text-muted">{cardsMsg}</span>}
            </div>
          </div>

          {/* Coach available while exploring */}
          <div className="openings__chat-col">
            <CoachChat userId={user.id} fen={node.fen} placeholder="Ask about this opening…" />
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
