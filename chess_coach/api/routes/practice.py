"""Practice game WebSocket endpoint.

The client connects to ``/api/practice/ws`` with JSON query-params that
identify the user, the opponent profile, the user's color, and (optionally)
an opening_node_id + opening_moves to start from.

Message protocol (all JSON):

    client -> server  {"type": "move", "uci": "e2e4"}
    client -> server  {"type": "resign"}

    server -> client  {"type": "move", "uci": "...", "fen": "..."}
    server -> client  {"type": "game_over", "result": "win|loss|draw",
                       "debrief": "...", "accuracy": 87.4,
                       "critical_moments": [...]}
    server -> client  {"type": "error", "message": "..."}
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ...maia.game_loop import (
    PracticeGame,
    finish_session,
    get_engine_for_opponent,
    play_engine_move,
    play_user_move,
    start_session,
)


router = APIRouter()


@router.websocket("/ws")
async def practice_ws(ws: WebSocket) -> None:
    await ws.accept()
    try:
        params = ws.query_params
        user_id = int(params.get("user_id", 0))
        opponent = params.get("opponent", "")
        user_color = params.get("user_color", "white")
        opening_node_id = int(params["opening_node_id"]) if params.get("opening_node_id") else None
        opening_moves_raw = params.get("opening_moves", "")

        if not user_id or not opponent:
            await ws.send_json({"type": "error", "message": "user_id and opponent required"})
            await ws.close()
            return

        game = PracticeGame(
            user_id=user_id,
            opponent=opponent,
            user_color=user_color,
            opening_node_id=opening_node_id,
        )
        if opening_moves_raw:
            for uci in opening_moves_raw.split(","):
                uci = uci.strip()
                if uci:
                    game.board.push_uci(uci)

        engine = await get_engine_for_opponent(opponent)
        await start_session(game)

        async def send_engine_move() -> None:
            mv = await play_engine_move(game, engine)
            await ws.send_json({"type": "move", "uci": mv.uci(), "fen": game.board.fen()})

        # If Maia plays first, send a move before reading from the user.
        if not game.is_user_turn() and not game.board.is_game_over():
            await send_engine_move()

        while not game.board.is_game_over():
            msg_text = await ws.receive_text()
            try:
                msg = json.loads(msg_text)
            except json.JSONDecodeError:
                await ws.send_json({"type": "error", "message": "bad JSON"})
                continue

            mtype = msg.get("type")
            if mtype == "move":
                try:
                    await play_user_move(game, msg["uci"])
                except (KeyError, ValueError) as exc:
                    await ws.send_json({"type": "error", "message": str(exc)})
                    continue
                if game.board.is_game_over():
                    break
                await send_engine_move()
            elif mtype == "resign":
                break
            else:
                await ws.send_json({"type": "error", "message": f"unknown type {mtype}"})

        result = await finish_session(game)
        await ws.send_json({"type": "game_over", **result})
        await ws.close()

    except WebSocketDisconnect:
        return
    except Exception as exc:
        try:
            await ws.send_json({"type": "error", "message": str(exc)})
        finally:
            await ws.close()
