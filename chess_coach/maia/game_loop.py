"""End-to-end practice game loop against a maia-individual model.

The API layer spins up a ``PracticeGame`` and relays moves between the user
(over WebSocket) and lc0 (via ``LC0Engine``). Once the game ends we call
Stockfish for accuracy analysis and hand the result to the debrief generator.
"""
from __future__ import annotations

import asyncio
import io
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import chess
import chess.pgn

from .. import db
from ..analysis.debrief import generate_debrief
from ..engine.stockfish import StockfishPool, score_to_cp
from .lc0_engine import LC0Engine, get_engine_for_opponent


@dataclass
class PracticeGame:
    user_id: int
    opponent: str
    user_color: str
    opening_node_id: int | None = None
    board: chess.Board = field(default_factory=chess.Board)
    move_history: list[chess.Move] = field(default_factory=list)
    repertoire_bias: dict[str, float] = field(default_factory=dict)
    session_id: int | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def is_user_turn(self) -> bool:
        return (self.board.turn == chess.WHITE) == (self.user_color == "white")

    def to_pgn(self) -> str:
        game = chess.pgn.Game()
        game.headers["Event"] = f"Practice vs {self.opponent}"
        game.headers["White"] = "User" if self.user_color == "white" else self.opponent
        game.headers["Black"] = self.opponent if self.user_color == "white" else "User"
        node = game
        for mv in self.move_history:
            node = node.add_variation(mv)
        return str(game)


async def _load_repertoire_bias(opponent: str, depth: int = 12) -> dict[str, float]:
    """Count how often ``opponent`` plays each move across their stored
    games, keyed by ``(fen_canonical, uci)``. For the game loop we collapse
    this to a flat uci->weight map that we look up per position at move time.
    """
    # Simple approach: the bias is rebuilt per position on demand, not once
    # up front. Return empty here and let ``get_position_bias`` do the work.
    return {}


async def get_position_bias(opponent: str, board: chess.Board, *, k: int = 20) -> dict[str, float]:
    """For the current position, return a probability over moves that the
    opponent has actually played in this (or transposed) position in their
    imported game history."""
    canon = " ".join(board.fen().split()[:4])
    # Walk opponent_games looking for this exact canonical FEN.
    rows = await db.fetch(
        """
        SELECT pgn, opponent_color FROM opponent_games
        WHERE opponent = $1
        ORDER BY played_at DESC
        LIMIT 500
        """,
        opponent,
    )
    counts: dict[str, int] = {}
    matches = 0
    for r in rows:
        game = chess.pgn.read_game(io.StringIO(r["pgn"]))
        if game is None:
            continue
        b = game.board()
        for mv in game.mainline_moves():
            if " ".join(b.fen().split()[:4]) == canon and (
                (b.turn == chess.WHITE) == (r["opponent_color"] == "white")
            ):
                counts[mv.uci()] = counts.get(mv.uci(), 0) + 1
                matches += 1
                break
            b.push(mv)
        if matches >= k:
            break

    total = sum(counts.values())
    if total == 0:
        return {}
    return {m: c / total for m, c in counts.items()}


async def play_engine_move(game: PracticeGame, engine: LC0Engine) -> chess.Move:
    bias = await get_position_bias(game.opponent, game.board)
    move = await engine.sample_move(game.board, repertoire_bias=bias, temperature=0.9)
    game.board.push(move)
    game.move_history.append(move)
    return move


async def play_user_move(game: PracticeGame, move_uci: str) -> chess.Move:
    move = chess.Move.from_uci(move_uci)
    if move not in game.board.legal_moves:
        raise ValueError(f"illegal move {move_uci} from {game.board.fen()}")
    game.board.push(move)
    game.move_history.append(move)
    return move


async def start_session(game: PracticeGame) -> int:
    row = await db.fetchrow(
        """
        INSERT INTO practice_sessions (
            user_id, session_type, opening_node_id, opponent_profile,
            fen_start, user_color, started_at
        ) VALUES ($1, 'full_game', $2, $3, $4, $5, $6)
        RETURNING id
        """,
        game.user_id,
        game.opening_node_id,
        game.opponent,
        game.board.fen(),
        game.user_color,
        game.started_at,
    )
    game.session_id = int(row["id"])
    return game.session_id


async def finish_session(game: PracticeGame, pool: StockfishPool | None = None) -> dict[str, Any]:
    """End-of-game: compute accuracy, find critical moments, generate a
    debrief, and persist everything."""
    pool_owned = pool is None
    pool = pool or StockfishPool(size=1)
    try:
        critical, accuracy = await _analyse_game(game, pool)
        debrief = await generate_debrief(
            pgn=game.to_pgn(),
            user_color=game.user_color,
            opening_node_id=game.opening_node_id,
            result=_game_result_label(game),
            critical_moments=critical,
        )
        await db.execute(
            """
            UPDATE practice_sessions
               SET pgn = $1,
                   fen_final = $2,
                   result = $3,
                   move_count = $4,
                   accuracy = $5,
                   critical_moments = $6,
                   debrief_text = $7,
                   ended_at = NOW(),
                   duration_sec = EXTRACT(EPOCH FROM (NOW() - started_at))::int
             WHERE id = $8
            """,
            game.to_pgn(),
            game.board.fen(),
            _game_result_label(game),
            len(game.move_history),
            accuracy,
            json.dumps(critical),
            debrief,
            game.session_id,
        )
        return {
            "accuracy": accuracy,
            "critical_moments": critical,
            "debrief": debrief,
            "result": _game_result_label(game),
        }
    finally:
        if pool_owned:
            await pool.close()


def _game_result_label(game: PracticeGame) -> str:
    outcome = game.board.outcome(claim_draw=True)
    if outcome is None:
        return "abandoned"
    if outcome.winner is None:
        return "draw"
    user_white = game.user_color == "white"
    user_won = outcome.winner == chess.WHITE if user_white else outcome.winner == chess.BLACK
    return "win" if user_won else "loss"


async def _analyse_game(game: PracticeGame, pool: StockfishPool) -> tuple[list[dict[str, Any]], float]:
    board = chess.Board()
    critical: list[dict[str, Any]] = []
    accuracies: list[float] = []
    user_white = game.user_color == "white"

    for ply, move in enumerate(game.move_history):
        is_user = (board.turn == chess.WHITE) == user_white
        if is_user:
            info = await pool.analyse(board, nodes=1_000_000, multipv=1)
            if info:
                best_cp = score_to_cp(info[0]["score"].pov(board.turn))
                board_after = board.copy()
                board_after.push(move)
                info2 = await pool.analyse(board_after, nodes=500_000, multipv=1)
                played_cp = -score_to_cp(info2[0]["score"].pov(board_after.turn)) if info2 else best_cp
                swing = best_cp - played_cp
                # Simple "accuracy" proxy: map swing to [0,100].
                acc = max(0.0, 100.0 - swing / 10.0)
                accuracies.append(acc)
                if swing >= 200:
                    critical.append({
                        "ply": ply,
                        "move_number": ply // 2 + 1,
                        "played": move.uci(),
                        "best": info[0]["pv"][0].uci() if info[0].get("pv") else None,
                        "eval_swing": swing,
                        "eval_before_cp": best_cp,
                    })
        board.push(move)

    avg_accuracy = sum(accuracies) / len(accuracies) if accuracies else 0.0
    return critical, avg_accuracy


async def play_practice_game(
    user_id: int,
    opponent: str,
    user_color: str,
    *,
    opening_moves: list[str] | None = None,
    opening_node_id: int | None = None,
    move_stream=None,  # async iterator yielding user UCI moves
    send_move=None,    # async callable (uci: str) -> None to push engine moves
) -> dict[str, Any]:
    """Top-level coroutine driving a full game. ``move_stream`` is produced
    by the WebSocket handler; ``send_move`` pushes engine moves back to the
    client.
    """
    game = PracticeGame(
        user_id=user_id,
        opponent=opponent,
        user_color=user_color,
        opening_node_id=opening_node_id,
    )
    if opening_moves:
        for uci in opening_moves:
            game.board.push_uci(uci)
            game.move_history.append(chess.Move.from_uci(uci))

    engine = await get_engine_for_opponent(opponent)
    await start_session(game)

    # If Maia plays first, push an engine move before reading from the user.
    if not game.is_user_turn():
        mv = await play_engine_move(game, engine)
        if send_move is not None:
            await send_move(mv.uci())

    if move_stream is not None:
        async for user_uci in move_stream:
            await play_user_move(game, user_uci)
            if game.board.is_game_over():
                break
            mv = await play_engine_move(game, engine)
            if send_move is not None:
                await send_move(mv.uci())
            if game.board.is_game_over():
                break

    return await finish_session(game)
