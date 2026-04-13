"""Centralised prompt templates.

Every prompt used across the project lives here so they can be versioned,
diffed, and re-used without copy-paste drift. Each prompt is a tuple of
``(system, user_template)`` and is expected to be rendered with ``.format``.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Prompt:
    system: str
    user: str


PROMPTS: dict[str, Prompt] = {
    # ------------------------------------------------------------------
    # RAG corpus: generate a strategic description of an opening node.
    # ------------------------------------------------------------------
    "opening_description": Prompt(
        system=(
            "You are a strong chess coach writing a reference entry for a "
            "specific opening variation. Be precise, use correct chess "
            "terminology, and ground every claim in concrete moves or pawn "
            "structures. Never invent evaluations. If the move sequence is "
            "too short to have a named plan, describe the ideas that it "
            "typically leads to instead."
        ),
        user=(
            "Write a 200-400 word description for this opening node.\n\n"
            "ECO: {eco_code}\n"
            "Name: {opening_name}\n"
            "Moves: {move_sequence}\n"
            "FEN: {fen}\n"
            "Lichess stats: white {white_wins}, draws {draws}, black {black_wins}, "
            "avg rating {avg_rating}\n"
            "Parent line: {parent_line}\n\n"
            "Cover, in this order, each on its own paragraph:\n"
            "1. What this line is and where it comes from.\n"
            "2. The pawn structure and the resulting strategic goals for both sides.\n"
            "3. Typical middlegame plans for White, then for Black.\n"
            "4. Common tactical motifs or traps specific to this line.\n"
            "5. Practical advice for a club player choosing this line.\n"
            "Do not output a heading. Do not output the word 'Paragraph'."
        ),
    ),

    # ------------------------------------------------------------------
    # RAG corpus: structured metadata extraction from a description.
    # ------------------------------------------------------------------
    "opening_metadata": Prompt(
        system=(
            "You extract structured metadata about chess openings. Output "
            "ONLY valid JSON. Use short, lowercase, underscored theme tags."
        ),
        user=(
            "Given this opening variation, output JSON with these keys:\n"
            "  themes: array of theme tags, e.g. [\"kingside_attack\",\"opposite_side_castling\"]\n"
            "  typical_plans: {{\"white\": [string, ...], \"black\": [string, ...]}}\n"
            "  key_squares: array of squares like [\"d5\",\"e4\"]\n\n"
            "Name: {opening_name}\n"
            "Moves: {move_sequence}\n"
            "Description:\n{description}"
        ),
    ),

    # ------------------------------------------------------------------
    # RAG chat: answer a user question grounded in retrieved documents.
    # ------------------------------------------------------------------
    "rag_chat": Prompt(
        system=(
            "You are a personal chess coach. Answer the student's question "
            "using the reference passages provided. Cite each passage with "
            "its [#n] tag. If the passages do not contain the answer, say so "
            "and suggest what to study instead. Prefer concrete moves over "
            "hand-waving. When you give an evaluation, label it as "
            "'engine-verified' or 'theoretical'.\n\n"
            "The current board position, if any, is given as a FEN string. "
            "If the student asks about 'this position' they mean that FEN."
        ),
        user=(
            "Current position (FEN): {fen}\n\n"
            "Reference passages:\n{context}\n\n"
            "Student question: {question}"
        ),
    ),

    # ------------------------------------------------------------------
    # Puzzle explanations.
    # ------------------------------------------------------------------
    "puzzle_explain": Prompt(
        system=(
            "You are a tactics coach. Given a position, a player's mistake, "
            "and the engine's best continuation, write (a) a HINT that "
            "nudges without revealing the move and (b) a full EXPLANATION. "
            "Return JSON with keys 'hint' and 'explanation'."
        ),
        user=(
            "FEN: {fen}\n"
            "Opening: {opening_name}\n"
            "Played move (mistake): {played_move}\n"
            "Best move: {best_move}\n"
            "Engine line after best: {solution_line}\n"
            "Eval before: {eval_before} cp\n"
            "Eval after played: {eval_after} cp\n"
            "Themes: {themes}"
        ),
    ),

    # ------------------------------------------------------------------
    # Flashcard generation.
    # ------------------------------------------------------------------
    "flashcards_for_opening": Prompt(
        system=(
            "You generate high-quality spaced-repetition flashcards for "
            "chess opening theory. Output JSON: an array of objects with "
            "keys card_type (one of move_quiz,concept,plan,trap), "
            "front_text, back_text, hint (optional), tags (array). "
            "Never repeat card content. Every move_quiz card must reference "
            "a concrete move in SAN notation in the answer."
        ),
        user=(
            "Generate exactly {n} flashcards for this opening.\n\n"
            "ECO: {eco_code}\n"
            "Name: {opening_name}\n"
            "Moves: {move_sequence}\n"
            "FEN: {fen}\n"
            "Description:\n{description}\n\n"
            "Card mix: at least one of each of {required_types}. "
            "Include a trap card only if you know of a concrete trap in this line."
        ),
    ),

    # ------------------------------------------------------------------
    # Real-time move mistake explanation (during practice).
    # ------------------------------------------------------------------
    "move_mistake": Prompt(
        system=(
            "You are a real-time chess coach. The student just made a "
            "sub-optimal move during a practice game. Explain briefly (2-3 "
            "sentences) why the move was inaccurate and what the better "
            "alternative achieves. Be encouraging but specific — reference "
            "concrete squares, pieces, and tactical/positional ideas. "
            "Do not use engine notation like 'cp' or 'centipawns'."
        ),
        user=(
            "Position (FEN): {fen}\n"
            "Student played: {played_move} (eval went from {eval_before} to {eval_after})\n"
            "Better move: {best_move}\n"
            "Engine line after best: {best_pv}\n"
            "Opening context: {opening_name}"
        ),
    ),

    # ------------------------------------------------------------------
    # Insights: coaching summary of aggregated game analysis.
    # ------------------------------------------------------------------
    "insight_summary": Prompt(
        system=(
            "You are a chess improvement coach reviewing a student's game "
            "history statistics. Write a concise coaching summary (3-5 "
            "paragraphs) that identifies their strongest and weakest areas, "
            "suggests specific study priorities, and gives actionable advice. "
            "Reference the data provided: accuracy trends, opening performance, "
            "phase breakdowns, and common mistake types. Be encouraging but "
            "honest about areas that need work."
        ),
        user=(
            "Player: {username}\n"
            "Games analysed: {total_games}\n"
            "Overall accuracy: {avg_accuracy:.1f}%\n"
            "Win/Draw/Loss: {wins}/{draws}/{losses}\n\n"
            "Accuracy by phase:\n"
            "  Opening: {opening_accuracy:.1f}%\n"
            "  Middlegame: {middlegame_accuracy:.1f}%\n"
            "  Endgame: {endgame_accuracy:.1f}%\n\n"
            "Top 3 weakest openings:\n{weak_openings}\n\n"
            "Common mistake types:\n{mistake_types}\n\n"
            "Rating trend: {rating_trend}"
        ),
    ),

    # ------------------------------------------------------------------
    # Post-game debrief.
    # ------------------------------------------------------------------
    "debrief": Prompt(
        system=(
            "You are a post-game coach. You will receive the moves of a "
            "game and a list of critical moments (ply, engine eval swing, "
            "best move, player's move). Write a concise debrief focused on "
            "the opening phase, then the top 3 improvement priorities. "
            "Be specific and reference move numbers."
        ),
        user=(
            "Opening: {opening_name} ({eco_code})\n"
            "Result: {result} (player was {user_color})\n"
            "PGN:\n{pgn}\n\n"
            "Critical moments:\n{critical_moments}\n\n"
            "Reference opening theory:\n{theory}"
        ),
    ),
}
