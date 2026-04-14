import type {
  AnalyseResponse,
  ChatResponse,
  Flashcard,
  GameAnalysis,
  GameInsights,
  GameOverPayload,
  OnboardingStatus,
  OpeningNode,
  PageContext,
  Puzzle,
  PuzzleAttemptResult,
  RecommendedOpponent,
  ReviewResult,
  Weakness,
} from "../types";

const BASE = "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status}: ${text}`);
  }
  return res.json();
}

// ── Health ───────────────────────────────────────────────────────────────
export const health = () => request<{ status: string }>("/health");

// ── Chat ─────────────────────────────────────────────────────────────────
export function chat(body: {
  user_id: number;
  question: string;
  fen?: string | null;
  conversation_id?: number | null;
  use_agent?: boolean;
  page_context?: PageContext | null;
}) {
  return request<ChatResponse>("/chat", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// ── Onboarding ──────────────────────────────────────────────────────────
export const getOnboardingStatus = (userId?: number) =>
  request<OnboardingStatus>(
    `/onboarding/status${userId != null ? `?user_id=${userId}` : ""}`
  );

export function startOnboarding(body: { chesscom_username: string }) {
  return request<{ user_id: number; started: boolean }>("/onboarding/start", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// ── Openings ─────────────────────────────────────────────────────────────
export const getOpening = (id: number) =>
  request<OpeningNode>(`/openings/${id}`);

export const searchOpenings = (q: string) =>
  request<{ results: OpeningNode[] }>(`/openings/search/by-name?q=${encodeURIComponent(q)}`);

export const getOpeningByFen = (fen: string) =>
  request<OpeningNode>(`/openings/by-fen/${encodeURIComponent(fen)}`);

// ── Flashcards ───────────────────────────────────────────────────────────
export const getDueCards = (userId: number, limit = 20) =>
  request<{ cards: Flashcard[] }>(`/flashcards/due?user_id=${userId}&limit=${limit}`);

export function generateCards(userId: number, openingNodeId: number, n = 6) {
  return request<{ created: number[] }>("/flashcards/generate", {
    method: "POST",
    body: JSON.stringify({ user_id: userId, opening_node_id: openingNodeId, n }),
  });
}

export function reviewCard(
  cardId: number,
  body: { user_id: number; rating: number; review_duration_ms?: number }
) {
  return request<ReviewResult>(`/flashcards/${cardId}/review`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// ── Puzzles ──────────────────────────────────────────────────────────────
export const nextPuzzle = (userId: number, openingNodeId?: number) => {
  const params = new URLSearchParams();
  if (openingNodeId != null) params.set("opening_node_id", String(openingNodeId));
  return request<Puzzle>(`/puzzles/next/${userId}?${params}`);
};

export function submitAttempt(
  puzzleId: number,
  body: {
    user_id: number;
    solved: boolean;
    moves_played?: string[];
    time_spent_ms?: number;
    hint_used?: boolean;
  }
) {
  return request<PuzzleAttemptResult>(`/puzzles/${puzzleId}/attempt`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// ── Games / weaknesses ───────────────────────────────────────────────────
export function importGames(body: {
  user_id: number;
  chess_com_username: string;
  months_back?: number;
}) {
  return request<{ inserted: number }>("/games/import", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function importOpponent(body: {
  opponent: string;
  months_back?: number;
}) {
  return request<{ inserted: number }>("/games/import-opponent", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export const recomputeWeaknesses = (userId: number) =>
  request<{ updated: number }>(`/games/weaknesses/${userId}/recompute`, {
    method: "POST",
  });

export const getWeaknesses = (userId: number, limit = 10) =>
  request<{ weaknesses: Weakness[] }>(`/games/weaknesses/${userId}?limit=${limit}`);

// ── Training ─────────────────────────────────────────────────────────────
export function startTraining(body: {
  opponent: string;
  steps?: number;
  min_games?: number;
}) {
  return request<{ status: string }>("/training/maia-individual", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export const trainingStatus = (opponent: string) =>
  request<{ status: string; error?: string }>(
    `/training/maia-individual/${encodeURIComponent(opponent)}`
  );

// ── Stockfish analysis ──────────────────────────────────────────────
export function analyse(body: {
  fen: string;
  depth?: number;
  multipv?: number;
  nodes?: number;
}) {
  return request<AnalyseResponse>("/analyse", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// ── Insights ────────────────────────────────────────────────────────
export const getInsights = (userId: number, monthsBack = 12) =>
  request<GameInsights>(
    `/insights/${userId}?months_back=${monthsBack}`
  );

export const analyseAllGames = (userId: number) =>
  request<{ status: string; message: string }>(
    `/insights/${userId}/analyse-all`,
    { method: "POST" }
  );

export const getGameAnalysis = (userId: number, gameId: number) =>
  request<GameAnalysis>(`/insights/${userId}/game/${gameId}`);

export const getCoachingSummary = (userId: number) =>
  request<{ summary: string }>(`/insights/${userId}/coaching-summary`, {
    method: "POST",
  });

// ── Opponents ────────────────────────────────────────────────────────────
export const getRecommendedOpponents = (userId: number, limit = 20) =>
  request<{ opponents: RecommendedOpponent[] }>(
    `/opponents/recommended/${userId}?limit=${limit}`
  );

// ── Practice WebSocket ───────────────────────────────────────────────────
export function connectPracticeWS(params: {
  user_id: number;
  opponent: string;
  user_color: string;
  opening_node_id?: number;
  opening_moves?: string;
}): WebSocket {
  const qs = new URLSearchParams();
  qs.set("user_id", String(params.user_id));
  qs.set("opponent", params.opponent);
  qs.set("user_color", params.user_color);
  if (params.opening_node_id != null)
    qs.set("opening_node_id", String(params.opening_node_id));
  if (params.opening_moves) qs.set("opening_moves", params.opening_moves);
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return new WebSocket(`${proto}//${window.location.host}/api/practice/ws?${qs}`);
}
