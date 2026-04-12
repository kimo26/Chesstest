"""Extract personalized tactical puzzles from a user's imported games.

Algorithm: walk every game ply, evaluate the position before each of the
player's moves, evaluate the position after the move, and compute the
centipawn swing. If the swing exceeds ``settings.puzzle_swing_cp`` AND a
single clear best move exists (gap to second-best ≥
``settings.puzzle_second_best_gap_cp``), emit a puzzle row.

The LLM never invents puzzles — it only writes the hint + explanation once
Stockfish has already identified the position.
"""
from __future__ import annotations

import io
import json
from dataclasses import dataclass, asdict
from typing import Any, Iterable, Sequence

import chess
import chess.engine
import chess.pgn

from .. import db
from ..config import settings
from ..llm import get_client
from ..llm.prompts import PROMPTS
from .stockfish import StockfishPool, score_to_cp


MATE_CP = 10_000


@dataclass
class ExtractedPuzzle:
    fen: str
    setup_move_uci: str | None
    played_move_uci: str
    best_move_uci: str
    solution_uci: list[str]
    solution_san: list[str]
    eval_before: int
    eval_after: int
    eval_swing: int
    ply: int
    piece_count: int
    themes: list[str]


def _pov_score_from_white(info: chess.engine.InfoDict, board: chess.Board) -> int:
    """Return the eval in centipawns from ``board.turn``'s POV."""
    pov = info["score"].pov(board.turn)
    return score_to_cp(pov, mate_cp=MATE_CP)


def _detect_themes(board_before: chess.Board, best_move: chess.Move) -> list[str]:
    themes: list[str] = []
    board = board_before.copy()
    attacker = board.piece_at(best_move.from_square)
    victim = board.piece_at(best_move.to_square)

    if victim is not None:
        themes.append("capture")
    if board.gives_check(best_move):
        themes.append("check")
    if best_move.promotion:
        themes.append("promotion")

    board.push(best_move)
    if board.is_checkmate():
        themes.append("mate")

    # Simple fork/pin/discovery heuristics
    if attacker and attacker.piece_type == chess.KNIGHT:
        # Is the knight attacking ≥2 pieces worth ≥3 after the move?
        attacks = list(board.attacks(best_move.to_square))
        valuable = [
            s for s in attacks
            if (p := board.piece_at(s)) and p.color != attacker.color and p.piece_type >= chess.KNIGHT
        ]
        if len(valuable) >= 2:
            themes.append("fork")

    return themes or ["calculation"]


async def extract_puzzles_from_game(
    pgn_text: str,
    user_color: str,
    *,
    pool: StockfishPool,
    nodes: int | None = None,
) -> list[ExtractedPuzzle]:
    nodes = nodes or settings.puzzle_stockfish_nodes
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None:
        return []
    board = game.board()
    puzzles: list[ExtractedPuzzle] = []
    user_is_white = user_color == "white"

    moves = list(game.mainline_moves())
    for ply, move in enumerate(moves):
        is_user_move = (board.turn == chess.WHITE) == user_is_white
        if is_user_move:
            # Eval BEFORE the user plays, with multipv=2 so we can check the
            # gap to the second-best alternative.
            info_before = await pool.analyse(board, nodes=nodes, multipv=2)
            if not info_before:
                board.push(move); continue
            best_pov = _pov_score_from_white(info_before[0], board)
            second_pov = (
                _pov_score_from_white(info_before[1], board)
                if len(info_before) > 1 else best_pov - 10_000
            )
            gap = best_pov - second_pov

            # Apply the played move and re-eval.
            board_after = board.copy()
            board_after.push(move)
            info_after = await pool.analyse(
                board_after, nodes=nodes // 2, multipv=1
            )
            if not info_after:
                board.push(move); continue
            played_pov = -_pov_score_from_white(info_after[0], board_after)

            swing = best_pov - played_pov

            if (
                swing >= settings.puzzle_swing_cp
                and gap >= settings.puzzle_second_best_gap_cp
            ):
                best_move = info_before[0]["pv"][0]
                solution_uci = [m.uci() for m in info_before[0]["pv"][:6]]
                # Produce SAN for the line
                san_board = board.copy()
                solution_san: list[str] = []
                for umove in solution_uci:
                    mv = chess.Move.from_uci(umove)
                    solution_san.append(san_board.san(mv))
                    san_board.push(mv)

                setup_move_uci = None
                if ply > 0:
                    setup_move_uci = moves[ply - 1].uci()

                puzzles.append(
                    ExtractedPuzzle(
                        fen=board.fen(),
                        setup_move_uci=setup_move_uci,
                        played_move_uci=move.uci(),
                        best_move_uci=best_move.uci(),
                        solution_uci=solution_uci,
                        solution_san=solution_san,
                        eval_before=best_pov,
                        eval_after=played_pov,
                        eval_swing=swing,
                        ply=ply,
                        piece_count=sum(1 for _ in board.piece_map()),
                        themes=_detect_themes(board, best_move),
                    )
                )

        board.push(move)

    return puzzles


async def _explain_puzzle(
    puzzle: ExtractedPuzzle,
    opening_name: str | None,
) -> tuple[str, str]:
    client = get_client()
    prompt = PROMPTS["puzzle_explain"]
    try:
        data = await client.generate_json(
            prompt.system,
            prompt.user.format(
                fen=puzzle.fen,
                opening_name=opening_name or "—",
                played_move=puzzle.played_move_uci,
                best_move=puzzle.best_move_uci,
                solution_line=" ".join(puzzle.solution_san),
                eval_before=puzzle.eval_before,
                eval_after=puzzle.eval_after,
                themes=", ".join(puzzle.themes),
            ),
            model=settings.ollama_fast_model,
        )
        return data.get("hint", ""), data.get("explanation", "")
    except Exception:
        return "", ""


async def _save_puzzle(
    user_game_id: int,
    opening_node_id: int | None,
    puzzle: ExtractedPuzzle,
    hint: str,
    explanation: str,
) -> int:
    canon = " ".join(puzzle.fen.split()[:4])
    # Initial rating heuristic: base 1200 + swing/2 + depth bonus.
    initial_rating = 1200 + puzzle.eval_swing / 4 + len(puzzle.solution_san) * 25
    initial_rating = float(max(800, min(2600, initial_rating)))

    row = await db.fetchrow(
        """
        INSERT INTO puzzles (
            source_game_id, opening_node_id, fen, fen_canonical, setup_move_uci,
            solution_uci, solution_san, played_move_uci,
            themes, opening_phase, piece_count, solution_depth,
            rating, eval_before, eval_after, eval_swing,
            hint_text, explanation, explanation_model, source
        ) VALUES (
            $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,
            $17,$18,$19,'generated'
        )
        RETURNING id
        """,
        user_game_id,
        opening_node_id,
        puzzle.fen,
        canon,
        puzzle.setup_move_uci,
        puzzle.solution_uci,
        puzzle.solution_san,
        puzzle.played_move_uci,
        puzzle.themes,
        puzzle.ply <= 30,
        puzzle.piece_count,
        len(puzzle.solution_san),
        initial_rating,
        float(puzzle.eval_before),
        float(puzzle.eval_after),
        float(puzzle.eval_swing),
        hint,
        explanation,
        settings.ollama_fast_model,
    )
    return int(row["id"])


async def extract_puzzles_for_user(
    user_id: int,
    *,
    limit_games: int = 50,
    pool: StockfishPool | None = None,
) -> int:
    owned_pool = pool is None
    pool = pool or StockfishPool(size=2)
    try:
        rows = await db.fetch(
            """
            SELECT g.id, g.pgn, g.user_color, g.opening_node_id, g.opening_name
            FROM user_games g
            WHERE g.user_id = $1
              AND NOT EXISTS (
                  SELECT 1 FROM puzzles p WHERE p.source_game_id = g.id
              )
            ORDER BY g.played_at DESC
            LIMIT $2
            """,
            user_id,
            limit_games,
        )
        n = 0
        for g in rows:
            puzzles = await extract_puzzles_from_game(
                g["pgn"], g["user_color"], pool=pool
            )
            for p in puzzles:
                hint, explanation = await _explain_puzzle(p, g["opening_name"])
                await _save_puzzle(
                    user_game_id=int(g["id"]),
                    opening_node_id=g["opening_node_id"],
                    puzzle=p,
                    hint=hint,
                    explanation=explanation,
                )
                n += 1
        return n
    finally:
        if owned_pool:
            await pool.close()
