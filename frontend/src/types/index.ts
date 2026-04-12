// ── Opening tree ──────────────────────────────────────────────────────────
export interface OpeningNode {
  id: number;
  parent_id: number | null;
  eco_code: string | null;
  opening_name: string | null;
  move_sequence: string;
  fen: string;
  depth: number;
  description: string | null;
  themes: string[] | null;
  typical_plans: Record<string, string[]> | null;
  key_squares: string[] | null;
  white_wins: number;
  draws: number;
  black_wins: number;
  total_games: number;
  children?: OpeningChild[];
}

export interface OpeningChild {
  id: number;
  opening_name: string | null;
  move_san: string | null;
  total_games: number;
}

// ── RAG chat ─────────────────────────────────────────────────────────────
export interface ChatSource {
  doc_id: number;
  title: string;
  chunk_level: string;
  opening_node_id: number | null;
  score: number;
  metadata: Record<string, unknown>;
}

export interface ChatResponse {
  answer: string;
  conversation_id: number;
  sources: ChatSource[];
}

// ── Flashcards ───────────────────────────────────────────────────────────
export interface Flashcard {
  id: number;
  opening_node_id: number | null;
  fen: string | null;
  card_type: "move_quiz" | "concept" | "plan" | "trap" | "transposition";
  front_text: string;
  back_text: string;
  hint: string | null;
  tags: string[];
  state: number;
  stability: number;
  difficulty: number;
  due: string;
}

export interface ReviewResult {
  state: number;
  stability: number;
  difficulty: number;
  due: string;
}

// ── Puzzles ──────────────────────────────────────────────────────────────
export interface Puzzle {
  id: number;
  fen: string;
  setup_move_uci: string | null;
  solution_uci: string[];
  solution_san: string[];
  played_move_uci: string | null;
  themes: string[];
  rating: number;
  eval_swing: number;
  hint_text: string | null;
  explanation: string | null;
  opening_node_id: number | null;
}

export interface PuzzleAttemptResult {
  user_rating: number;
  puzzle_rating: number;
}

// ── Games & weaknesses ───────────────────────────────────────────────────
export interface Weakness {
  opening_node_id: number;
  opening_name: string | null;
  eco_code: string | null;
  color: string;
  games_played: number;
  win_rate: number;
  avg_deviation_ply: number | null;
  weakness_score: number;
  weakness_type: string | null;
}

// ── Practice ─────────────────────────────────────────────────────────────
export interface PracticeConfig {
  user_id: number;
  opponent: string;
  user_color: "white" | "black";
  opening_node_id?: number;
  opening_moves?: string;
}

export interface CriticalMoment {
  ply: number;
  move_number: number;
  played: string;
  best: string | null;
  eval_swing: number;
  eval_before_cp: number;
}

export interface GameOverPayload {
  result: string;
  accuracy: number;
  critical_moments: CriticalMoment[];
  debrief: string;
}

// ── User & progress ─────────────────────────────────────────────────────
export interface User {
  id: number;
  username: string;
  chess_com_user: string | null;
  lichess_user: string | null;
  current_rating: Record<string, number>;
  puzzle_rating: number;
}
