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

export interface ToolCallTrace {
  name: string;
  args: Record<string, unknown>;
  result: Record<string, unknown>;
}

export interface ChatResponse {
  answer: string;
  conversation_id: number;
  sources: ChatSource[];
  tool_trace?: ToolCallTrace[] | null;
}

// ── Coach widget / page-context ─────────────────────────────────────────
// Tagged union of "where is the user right now?" payloads. The coach
// widget passes the active PageContext on every question so the agent
// can resolve "this card" / "why was that bad?" without the user
// restating the position.
export type PageContext =
  | { page: "practice"; game_id?: number; fen?: string; last_move?: string; eval_cp?: number; opponent?: string; user_color?: string }
  | { page: "flashcards"; card_id?: number; fen?: string | null; front?: string; back?: string; card_type?: string }
  | { page: "puzzles"; puzzle_id?: number; fen?: string; rating?: number; themes?: string[] }
  | { page: "openings"; fen?: string; opening_name?: string | null; eco_code?: string | null; opening_node_id?: number | null }
  | { page: "insights"; viewing_game_id?: number | null; avg_accuracy?: number }
  | { page: "progress" }
  | { page: "dashboard" }
  | { page: "chat" };

// ── Onboarding ──────────────────────────────────────────────────────────
export interface OnboardingStep {
  name: string;
  label: string;
  state: "pending" | "running" | "ok" | "error";
  eta_seconds: number;
  elapsed_seconds?: number | null;
  detail?: string | null;
  error?: string | null;
}

export interface OnboardingStatus {
  needs_onboarding: boolean;
  user_id: number | null;
  chesscom_username: string | null;
  in_progress: boolean;
  completed: boolean;
  steps: OnboardingStep[];
  current_step: string | null;
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

// ── Opponents ────────────────────────────────────────────────────────────
export interface RecommendedOpponent {
  opponent: string;
  games: number;
  wins: number;
  draws: number;
  losses: number;
  win_rate: number;
  peak_rating: number | null;
  last_played: string | null;
  training_games: number;
  has_trained_model: boolean;
  score: number;
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

// ── Stockfish analysis ──────────────────────────────────────────────────
export interface AnalysisLine {
  pv: string[];
  cp: number | null;
  mate: number | null;
  depth: number;
}

export interface AnalyseResponse {
  fen: string;
  lines: AnalysisLine[];
}

// ── Practice real-time events ───────────────────────────────────────────
export interface MistakeEvent {
  played: string;
  best: string | null;
  swing_cp: number;
  eval_before_cp: number;
  eval_after_cp: number;
  explanation: string;
  best_pv: string[];
}

export interface HintResponse {
  best_uci: string | null;
  pv: string[];
  cp: number | null;
  mate: number | null;
}

export interface EvalEvent {
  cp: number | null;
  mate: number | null;
}

// ── Insights ────────────────────────────────────────────────────────────
export interface MoveAnalysis {
  ply: number;
  move_number: number;
  move_uci: string;
  is_user_move: boolean;
  cp_before: number;
  cp_after: number;
  swing: number;
  accuracy: number;
  best_uci: string | null;
}

export interface GameAnalysis {
  game_id: number;
  user_color: string;
  result: string;
  played_at: string;
  eco_code: string | null;
  opening_name: string | null;
  opponent_name: string | null;
  avg_accuracy: number;
  phases: { opening: number; middlegame: number; endgame: number };
  blunders: number;
  mistakes: number;
  inaccuracies: number;
  moves: MoveAnalysis[];
}

export interface GameSummary {
  game_id: number;
  date: string;
  opponent: string | null;
  result: string;
  accuracy: number;
  eco_code: string | null;
  opening_name: string | null;
  blunders: number;
  mistakes: number;
}

export interface OpeningPerformance {
  eco_code: string;
  opening_name: string;
  games: number;
  win_rate: number;
  avg_accuracy: number;
}

export interface GameInsights {
  total_games: number;
  avg_accuracy: number;
  wins: number;
  draws: number;
  losses: number;
  accuracy_over_time: { date: string; accuracy: number; game_id: number }[];
  phases: { opening: number; middlegame: number; endgame: number };
  opening_performance: OpeningPerformance[];
  mistake_counts: { blunders: number; mistakes: number; inaccuracies: number };
  games: GameSummary[];
  coaching_summary: string | null;
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
