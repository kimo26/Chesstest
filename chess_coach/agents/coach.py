"""Tool-calling chess coach (multi-agent-style orchestrator).

Design:
    Before this module, the chat endpoint was a straight RAG pipeline:
    retrieve -> prompt -> answer. That works for "explain the Najdorf"
    but falls apart when the user asks "analyse this position" (needs
    Stockfish), "what does X usually play here?" (needs opponent
    DB + lc0), or multi-step questions that mix theory + engine +
    history.

    Instead of forcing one prompt to juggle all of that, we wrap every
    backend capability as a *tool* and let a smaller LLM decide which
    one(s) to call. Each tool is a thin adapter over code that already
    existed:

        rag_search         -> rag.retriever.hybrid_search
        stockfish_analyse  -> engine.stockfish.StockfishPool
        opening_lookup     -> DB query on opening_nodes
        my_games_query     -> DB query on user_games
        opponent_tendency  -> DB query on opponent_games

    We use Ollama's native tool-calling schema. The loop runs for up to
    ``MAX_ITERATIONS`` rounds; each round either calls tools or returns
    a final answer. The full trace of tool calls is returned alongside
    the answer so the UI can render a "show reasoning" panel.

    Why not full multi-agent? For a single-user local app the overhead
    of multiple cooperating agents isn't worth it: one coach + many
    tools gives the same flexibility with 1/N the latency and token
    cost. This IS the multi-agent pattern — the "agents" are the tools
    themselves, each backed by a specialist subsystem.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import chess
import httpx

from .. import db
from ..config import settings
from ..engine.stockfish import StockfishPool, score_to_cp
from ..llm import get_client
from ..llm.ollama_client import OllamaError
from ..rag.retriever import hybrid_search


MAX_ITERATIONS = 6


# ── Tool definitions (Ollama / OpenAI-compatible JSON schema) ───────────

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "rag_search",
            "description": (
                "Search the indexed chess knowledge base (Wikipedia opening "
                "articles, Wikibooks strategy/endgame chapters, PDF books). "
                "Use this for any question about opening theory, principles, "
                "middlegame plans, or endgame technique."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural-language search query"},
                    "top_k": {"type": "integer", "default": 5},
                    "filter_eco": {"type": "string", "description": "Optional ECO code, e.g. 'B90'"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stockfish_analyse",
            "description": (
                "Run Stockfish 17 on a FEN and return the top engine lines "
                "with centipawn / mate evaluations. Use this for 'what's the "
                "best move?', 'is this position winning?', or 'why is my "
                "move bad?' questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "fen": {"type": "string"},
                    "depth": {"type": "integer", "default": 20},
                    "multipv": {"type": "integer", "default": 3},
                },
                "required": ["fen"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "opening_lookup",
            "description": (
                "Look up an opening node by FEN or by name. Returns "
                "opening_name, ECO, move sequence, Lichess aggregate stats, "
                "and our coaching description (if any)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "fen": {"type": "string"},
                    "name": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "my_games_query",
            "description": (
                "Query the logged-in user's own imported Chess.com games. "
                "Supports optional filters: ECO, color, opponent. Returns "
                "summary stats (win_rate, avg_accuracy, recent results)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "eco_code": {"type": "string"},
                    "color": {"type": "string", "enum": ["white", "black"]},
                    "opponent": {"type": "string"},
                    "limit": {"type": "integer", "default": 10},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "opponent_tendency",
            "description": (
                "For a specific opponent, report what they have historically "
                "played in the current position (from the opponent_games "
                "table). Returns a probability distribution over moves."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "opponent": {"type": "string"},
                    "fen": {"type": "string"},
                },
                "required": ["opponent", "fen"],
            },
        },
    },
]


# ── Tool implementations ────────────────────────────────────────────────

async def _tool_rag_search(args: dict) -> dict:
    q = args.get("query", "")
    top_k = int(args.get("top_k", 5))
    filter_eco = args.get("filter_eco") or None
    results = await hybrid_search(q, top_k=top_k, filter_eco=filter_eco)
    return {
        "passages": [
            {
                "title": r.title,
                "chunk_level": r.chunk_level,
                "content": r.content[:1200],
                "score": r.score,
            }
            for r in results
        ]
    }


async def _tool_stockfish(args: dict, pool: StockfishPool) -> dict:
    fen = args.get("fen", "")
    depth = int(args.get("depth", 20))
    multipv = int(args.get("multipv", 3))
    try:
        board = chess.Board(fen)
    except ValueError as exc:
        return {"error": f"invalid FEN: {exc}"}
    info = await pool.analyse(board, depth=depth, multipv=multipv)
    out = []
    for entry in info:
        score = entry.get("score")
        pv = [m.uci() for m in (entry.get("pv") or [])[:8]]
        cp = score_to_cp(score.pov(board.turn)) if score else None
        mate = score.pov(board.turn).mate() if score else None
        out.append({"pv": pv, "cp": cp, "mate": mate, "depth": entry.get("depth")})
    return {"fen": fen, "lines": out}


async def _tool_opening_lookup(args: dict) -> dict:
    fen = args.get("fen")
    name = args.get("name")
    if fen:
        canon = " ".join(fen.split()[:4])
        row = await db.fetchrow(
            """
            SELECT id, eco_code, opening_name, move_sequence, description,
                   white_wins, draws, black_wins
            FROM opening_nodes WHERE fen_canonical = $1
            """,
            canon,
        )
    elif name:
        row = await db.fetchrow(
            """
            SELECT id, eco_code, opening_name, move_sequence, description,
                   white_wins, draws, black_wins
            FROM opening_nodes
            WHERE opening_name ILIKE $1
            ORDER BY total_games DESC NULLS LAST
            LIMIT 1
            """,
            f"%{name}%",
        )
    else:
        return {"error": "need fen or name"}
    if row is None:
        return {"match": None}
    total = (row["white_wins"] or 0) + (row["draws"] or 0) + (row["black_wins"] or 0)
    return {
        "match": {
            "id": row["id"],
            "eco_code": row["eco_code"],
            "opening_name": row["opening_name"],
            "move_sequence": row["move_sequence"],
            "description": (row["description"] or "")[:2000],
            "white_win_rate": round((row["white_wins"] or 0) / total, 3) if total else None,
            "draw_rate": round((row["draws"] or 0) / total, 3) if total else None,
            "black_win_rate": round((row["black_wins"] or 0) / total, 3) if total else None,
            "total_games": total,
        }
    }


async def _tool_my_games(args: dict, user_id: int) -> dict:
    filters = []
    params: list = [user_id]
    if args.get("eco_code"):
        params.append(args["eco_code"])
        filters.append(f"AND eco_code = ${len(params)}")
    if args.get("color"):
        params.append(args["color"])
        filters.append(f"AND user_color = ${len(params)}")
    if args.get("opponent"):
        params.append(args["opponent"])
        filters.append(f"AND opponent_name ILIKE ${len(params)}")
    limit = int(args.get("limit", 10))
    params.append(limit)
    where = " ".join(filters)

    rows = await db.fetch(
        f"""
        SELECT eco_code, opening_name, user_color, opponent_name, result,
               accuracy, played_at
        FROM user_games
        WHERE user_id = $1 {where}
        ORDER BY played_at DESC
        LIMIT ${len(params)}
        """,
        *params,
    )
    # Aggregate stats over the full filter set.
    stats_params = params[:-1]  # drop limit
    stats = await db.fetchrow(
        f"""
        SELECT
            COUNT(*)::int AS total,
            SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END)::int AS wins,
            SUM(CASE WHEN result = 'draw' THEN 1 ELSE 0 END)::int AS draws,
            SUM(CASE WHEN result = 'loss' THEN 1 ELSE 0 END)::int AS losses,
            AVG(accuracy)::real AS avg_accuracy
        FROM user_games WHERE user_id = $1 {where}
        """,
        *stats_params,
    )

    total = (stats["total"] or 0) if stats else 0
    return {
        "summary": {
            "total": total,
            "wins": stats["wins"] if stats else 0,
            "draws": stats["draws"] if stats else 0,
            "losses": stats["losses"] if stats else 0,
            "avg_accuracy": round(stats["avg_accuracy"], 1)
            if stats and stats["avg_accuracy"] is not None
            else None,
            "win_rate": round((stats["wins"] or 0) / total, 3) if total else None,
        },
        "recent": [
            {
                "eco": r["eco_code"],
                "opening": r["opening_name"],
                "color": r["user_color"],
                "opponent": r["opponent_name"],
                "result": r["result"],
                "accuracy": r["accuracy"],
                "played_at": r["played_at"].isoformat() if r["played_at"] else None,
            }
            for r in rows
        ],
    }


async def _tool_opponent_tendency(args: dict) -> dict:
    # Delegate to existing helper.
    from ..maia.game_loop import get_position_bias

    opp = args.get("opponent", "")
    fen = args.get("fen", "")
    try:
        board = chess.Board(fen)
    except ValueError as exc:
        return {"error": f"invalid FEN: {exc}"}
    dist = await get_position_bias(opp, board, k=30)
    return {
        "opponent": opp,
        "fen": fen,
        "distribution": sorted(
            [{"move": m, "p": round(p, 3)} for m, p in dist.items()],
            key=lambda x: -x["p"],
        )[:8],
    }


TOOL_IMPLS: dict[str, Any] = {
    "rag_search": _tool_rag_search,
    "opening_lookup": _tool_opening_lookup,
    "my_games_query": _tool_my_games,  # special: needs user_id
    "opponent_tendency": _tool_opponent_tendency,
    # stockfish needs the pool; handled inline below
}


# ── Agent loop ──────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a personal chess coach with access to specialised tools.

Pick the right tool(s) for the user's question, call them (in parallel
when independent), and ground every claim in the results.

Rules:
* If the user references "this position" and a FEN is provided, use it
  verbatim for stockfish_analyse and opening_lookup.
* Cite retrieved passages by their title.
* If a tool returns an error, tell the user what you tried and why it
  failed — don't silently guess.
* When you have enough information, return the final answer. Do not
  hallucinate tool calls.
* Label engine evals as 'Stockfish depth D: +X.YZ' so the user knows
  where the number came from.
* When page_context is present, treat it as the user's current
  situation (which page they're on, active FEN, active flashcard /
  puzzle, etc.) and ground your answer in it. On the Practice page,
  questions like "why was that bad?" refer to ``last_move`` in the
  context; on Flashcards, "explain this card" refers to front/back.
* Remember earlier messages in the conversation — don't repeat
  boilerplate or ask the user to restate facts you already have.
"""


MEMORY_TURN_CAP = 12
"""Max prior user/assistant pairs to replay into the context window."""


async def _load_memory(conversation_id: int) -> list[dict]:
    """Fetch recent user/assistant messages for a conversation, oldest first."""
    rows = await db.fetch(
        """
        SELECT role, content
        FROM rag_messages
        WHERE conversation_id = $1
          AND role IN ('user','assistant')
        ORDER BY created_at DESC
        LIMIT $2
        """,
        conversation_id,
        MEMORY_TURN_CAP * 2,
    )
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


async def _get_or_create_widget_conversation(user_id: int) -> int:
    """Return the rolling ``kind='widget'`` conversation for the user.

    The persistent coach widget keeps one long-running conversation so the
    user's question history follows them across pages. Separate from the
    ad-hoc conversations the full-screen /chat page creates per thread.
    """
    row = await db.fetchrow(
        """
        SELECT id FROM rag_conversations
        WHERE user_id = $1 AND kind = 'widget'
        ORDER BY created_at DESC LIMIT 1
        """,
        user_id,
    )
    if row is not None:
        return int(row["id"])
    row = await db.fetchrow(
        """
        INSERT INTO rag_conversations (user_id, title, kind)
        VALUES ($1, 'Coach', 'widget')
        RETURNING id
        """,
        user_id,
    )
    return int(row["id"])


async def _persist_turn(
    conversation_id: int,
    question: str,
    answer_text: str,
    trace: list["ToolCallTrace"],
    context: dict | None,
) -> None:
    trace_json = json.dumps([tc.to_dict() for tc in trace]) if trace else None
    ctx_json = json.dumps(context) if context else None
    await db.execute(
        """
        INSERT INTO rag_messages
            (conversation_id, role, content, context_json, used_agent, model_used)
        VALUES ($1, 'user', $2, $3::jsonb, TRUE, $4)
        """,
        conversation_id,
        question,
        ctx_json,
        settings.ollama_gen_model,
    )
    await db.execute(
        """
        INSERT INTO rag_messages
            (conversation_id, role, content, context_json, tool_trace, used_agent, model_used)
        VALUES ($1, 'assistant', $2, $3::jsonb, $4::jsonb, TRUE, $5)
        """,
        conversation_id,
        answer_text,
        ctx_json,
        trace_json,
        settings.ollama_gen_model,
    )


@dataclass
class ToolCallTrace:
    name: str
    args: dict
    result: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"name": self.name, "args": self.args, "result": self.result}


async def _ollama_chat_with_tools(
    messages: list[dict], *, model: str | None = None
) -> dict:
    """Call Ollama's /api/chat with tools and return the raw message dict.

    We don't use our OllamaClient.chat wrapper because we need the raw
    message (for tool_calls) not just the content string.
    """
    payload: dict[str, Any] = {
        "model": model or settings.ollama_gen_model,
        "messages": messages,
        "stream": False,
        "tools": TOOLS,
        "options": {"temperature": 0.2, "num_ctx": settings.ollama_gen_num_ctx},
    }
    async with httpx.AsyncClient(timeout=settings.ollama_timeout_s) as client:
        resp = await client.post(f"{settings.ollama_url}/api/chat", json=payload)
        if resp.status_code >= 500:
            raise OllamaError(f"Ollama {resp.status_code}: {resp.text[:200]}")
        resp.raise_for_status()
        data = resp.json()
    return data.get("message", {})


async def answer(
    user_id: int,
    question: str,
    *,
    fen: str | None = None,
    pool: StockfishPool | None = None,
    conversation_id: int | None = None,
    page_context: dict | None = None,
    persist: bool = True,
) -> tuple[str, list[ToolCallTrace], int | None]:
    """Run the coach agent.

    Returns ``(answer_text, tool_trace, conversation_id)``.

    ``conversation_id``
        If provided, prior turns for that conversation are replayed so the
        agent has memory. If None and ``persist=True``, we pick/create the
        user's persistent widget conversation so the widget remembers across
        pages.

    ``page_context``
        Dict describing where the user is right now (page name, active FEN,
        card/puzzle id, etc.). Injected into the system prompt so the coach
        can ground "this position" / "this card" / "why was that bad?" style
        questions without the user restating them.

    ``pool``
        Optional shared ``StockfishPool``. If None we create a small one and
        tear it down at the end.
    """
    own_pool = pool is None
    if pool is None:
        pool = StockfishPool(size=1)

    # Resolve conversation: explicit > widget default.
    if conversation_id is None and persist:
        try:
            conversation_id = await _get_or_create_widget_conversation(user_id)
        except Exception:
            conversation_id = None

    system = SYSTEM_PROMPT
    if fen:
        system += f"\n\nThe current board FEN is: {fen}"
    if page_context:
        system += "\n\npage_context = " + json.dumps(page_context, default=str)

    messages: list[dict] = [{"role": "system", "content": system}]

    if conversation_id is not None:
        try:
            messages.extend(await _load_memory(conversation_id))
        except Exception:
            # If memory load fails (column missing on older schemas), skip.
            pass

    messages.append({"role": "user", "content": question})

    trace: list[ToolCallTrace] = []

    try:
        for _ in range(MAX_ITERATIONS):
            msg = await _ollama_chat_with_tools(messages)
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                # Final answer.
                text = msg.get("content", "").strip() or "(no answer)"
                if persist and conversation_id is not None:
                    try:
                        await _persist_turn(
                            conversation_id, question, text, trace, page_context
                        )
                    except Exception:
                        pass
                return text, trace, conversation_id

            # Append the assistant's tool-call message so the model sees
            # its own plan in the next turn.
            messages.append(msg)

            # Execute each tool call and append its result as a tool message.
            for call in tool_calls:
                fn = (call.get("function") or {})
                name = fn.get("name", "")
                raw_args = fn.get("arguments", {})
                if isinstance(raw_args, str):
                    try:
                        args = json.loads(raw_args)
                    except json.JSONDecodeError:
                        args = {}
                else:
                    args = dict(raw_args or {})

                tc = ToolCallTrace(name=name, args=args)
                try:
                    if name == "stockfish_analyse":
                        tc.result = await _tool_stockfish(args, pool)
                    elif name == "my_games_query":
                        tc.result = await _tool_my_games(args, user_id)
                    elif name in TOOL_IMPLS:
                        tc.result = await TOOL_IMPLS[name](args)
                    else:
                        tc.result = {"error": f"unknown tool {name}"}
                except Exception as exc:
                    tc.result = {"error": f"{type(exc).__name__}: {exc}"}
                trace.append(tc)

                messages.append({
                    "role": "tool",
                    "content": json.dumps(tc.result)[:8000],
                    "tool_name": name,
                })

        # Out of iterations — ask for a synthesis.
        messages.append(
            {"role": "user", "content": "Please give your final answer now based on what you have."}
        )
        final = await _ollama_chat_with_tools(messages)
        text = final.get("content", "(iteration limit reached)").strip()
        if persist and conversation_id is not None:
            try:
                await _persist_turn(conversation_id, question, text, trace, page_context)
            except Exception:
                pass
        return text, trace, conversation_id
    finally:
        if own_pool:
            await pool.close()
