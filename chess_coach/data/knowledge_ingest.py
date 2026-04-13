"""Broader knowledge base ingestion pipelines.

Ingests chess theory content beyond just opening descriptions:

1. **Wikibooks Chess** — strategy, middlegame, endgame chapters from
   ``en.wikibooks.org/wiki/Chess``.
2. **Lichess puzzle DB** — rated community puzzles from the Lichess
   open database.
3. **Annotated PGN files** — parse ``.pgn`` files with comments and
   store annotated positions as RAG documents.

All text content is chunked, embedded, and stored in ``rag_documents``
so the RAG retriever can surface strategy/endgame/middlegame knowledge
during coach chat sessions.
"""
from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any

import chess
import chess.pgn
import httpx

from .. import db
from ..config import settings
from ..llm import get_client


# ── Wikibooks Chess ──────────────────────────────────────────────────────

WIKIBOOKS_API = "https://en.wikibooks.org/w/api.php"

# Key Wikibooks Chess pages covering strategy, tactics, endgames, etc.
WIKIBOOKS_PAGES = [
    "Chess/Tactics",
    "Chess/Strategy",
    "Chess/The_Endgame",
    "Chess/Middlegame",
    "Chess/Pawn_Structure",
    "Chess/Piece_Activity",
    "Chess/King_Safety",
    "Chess/Checkmates",
    "Chess/Opening_Principles",
    "Chess/Draws",
    "Chess/Basic_Endgames",
    "Chess/Rook_Endgames",
    "Chess/Pawn_Endgames",
    "Chess/Bishop_vs_Knight",
    "Chess/Exchange_Sacrifice",
    "Chess/Prophylaxis",
    "Chess/Calculation",
]


async def _fetch_wikibooks_page(title: str) -> dict[str, str] | None:
    """Fetch a Wikibooks page extract via the MediaWiki API."""
    params = {
        "action": "query",
        "titles": title,
        "prop": "extracts",
        "explaintext": "true",
        "format": "json",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(WIKIBOOKS_API, params=params)
        resp.raise_for_status()
        data = resp.json()
        pages = data.get("query", {}).get("pages", {})
        for page in pages.values():
            if page.get("missing") is not None:
                continue
            return {"title": page.get("title", title), "extract": page.get("extract", "")}
    return None


def _chunk_text(text: str, title: str, max_chars: int = 2000) -> list[dict[str, str]]:
    """Split a long text into overlapping chunks."""
    chunks = []
    paragraphs = text.split("\n\n")
    current = ""
    chunk_idx = 0

    for para in paragraphs:
        if len(current) + len(para) > max_chars and current:
            chunks.append({
                "title": f"{title} (part {chunk_idx + 1})",
                "content": current.strip(),
            })
            # 200-char overlap.
            current = current[-200:] + "\n\n" + para
            chunk_idx += 1
        else:
            current += "\n\n" + para if current else para

    if current.strip():
        chunks.append({
            "title": f"{title} (part {chunk_idx + 1})" if chunk_idx > 0 else title,
            "content": current.strip(),
        })
    return chunks


async def ingest_wikibooks(concurrency: int = 4) -> int:
    """Fetch and ingest all Wikibooks Chess pages into the RAG corpus."""
    client = get_client()
    count = 0

    for page_title in WIKIBOOKS_PAGES:
        result = await _fetch_wikibooks_page(page_title)
        if not result or not result["extract"]:
            continue

        # Determine chunk_level from title.
        title_lower = page_title.lower()
        if "endgame" in title_lower or "end_game" in title_lower:
            chunk_level = "endgame"
        elif "middlegame" in title_lower or "middle" in title_lower:
            chunk_level = "middlegame"
        elif "tactic" in title_lower or "checkmate" in title_lower or "calculation" in title_lower:
            chunk_level = "tactics"
        elif "strategy" in title_lower or "pawn_structure" in title_lower or "prophylaxis" in title_lower:
            chunk_level = "strategy"
        else:
            chunk_level = "general"

        attribution = f"\n\nSource: Wikibooks — {result['title']}, CC BY-SA 3.0."
        chunks = _chunk_text(result["extract"], result["title"])

        for chunk in chunks:
            content = chunk["content"] + attribution
            embedding = await client.embed_one(content)
            await db.execute(
                """
                INSERT INTO rag_documents (
                    opening_node_id, chunk_level, title, content, embedding,
                    metadata, source_type, source_model
                ) VALUES (NULL, $1, $2, $3, $4::vector, $5, 'wikibooks', 'wikipedia')
                """,
                chunk_level,
                chunk["title"],
                content,
                db.vector_literal(embedding),
                json.dumps({"wikibooks_page": page_title, "chunk_level": chunk_level}),
            )
            count += 1

    return count


# ── Lichess puzzle DB ────────────────────────────────────────────────────

async def ingest_lichess_puzzles(csv_path: str | Path, limit: int = 50000) -> int:
    """Import puzzles from the Lichess puzzle CSV file.

    The CSV has columns: PuzzleId, FEN, Moves, Rating, RatingDeviation,
    Popularity, NbPlays, Themes, GameUrl, OpeningTags.

    Download from: https://database.lichess.org/lichess_db_puzzle.csv.zst
    (decompress with ``zstd -d`` first).
    """
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Lichess puzzle CSV not found at {path}")

    count = 0
    with open(path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if count >= limit:
                break

            fen = row.get("FEN", "")
            moves_str = row.get("Moves", "")
            rating = float(row.get("Rating", 1500))
            themes = row.get("Themes", "").split()
            puzzle_id = row.get("PuzzleId", "")

            if not fen or not moves_str:
                continue

            moves = moves_str.split()
            if len(moves) < 2:
                continue

            setup_move = moves[0]
            solution = moves[1:]

            # Convert solution to SAN.
            board = chess.Board(fen)
            board.push_uci(setup_move)
            solution_san = []
            temp_board = board.copy()
            for uci in solution:
                try:
                    mv = chess.Move.from_uci(uci)
                    solution_san.append(temp_board.san(mv))
                    temp_board.push(mv)
                except Exception:
                    break

            await db.execute(
                """
                INSERT INTO puzzles (
                    fen, setup_move_uci, solution_uci, solution_san,
                    themes, rating, source
                ) VALUES ($1, $2, $3, $4, $5, $6, 'lichess')
                ON CONFLICT DO NOTHING
                """,
                board.fen(),
                setup_move,
                solution,
                solution_san,
                themes,
                rating,
            )
            count += 1

    return count


# ── Annotated PGN ingestion ─────────────────────────────────────────────

async def ingest_annotated_pgn(pgn_path: str | Path, limit: int = 1000) -> int:
    """Parse a PGN file with annotations/comments and store annotated
    positions as RAG documents."""
    path = Path(pgn_path)
    if not path.exists():
        raise FileNotFoundError(f"PGN file not found at {path}")

    client = get_client()
    count = 0

    with open(path, "r") as f:
        while count < limit:
            game = chess.pgn.read_game(f)
            if game is None:
                break

            event = game.headers.get("Event", "Unknown")
            white = game.headers.get("White", "?")
            black = game.headers.get("Black", "?")
            game_title = f"{white} vs {black} ({event})"

            # Walk the game tree and collect commented positions.
            node = game
            board = game.board()
            annotations: list[dict[str, str]] = []

            while node.variations:
                next_node = node.variation(0)
                board.push(next_node.move)
                comment = next_node.comment
                if comment and len(comment) > 20:
                    annotations.append({
                        "fen": board.fen(),
                        "move": board.peek().uci(),
                        "comment": comment,
                        "ply": board.ply(),
                    })
                node = next_node

            if not annotations:
                continue

            # Create a single RAG document per game with all annotations.
            content_parts = [f"Annotated game: {game_title}\n"]
            for ann in annotations:
                content_parts.append(
                    f"After move {ann['ply']//2 + 1} ({ann['move']}): {ann['comment']}"
                )
            content = "\n\n".join(content_parts)

            if len(content) > 4000:
                content = content[:4000]

            embedding = await client.embed_one(content)
            await db.execute(
                """
                INSERT INTO rag_documents (
                    opening_node_id, chunk_level, title, content, embedding,
                    metadata, source_type, source_model
                ) VALUES (NULL, 'general', $1, $2, $3::vector, $4, 'annotated_game', NULL)
                """,
                game_title,
                content,
                db.vector_literal(embedding),
                json.dumps({
                    "game_event": event,
                    "white": white,
                    "black": black,
                    "annotation_count": len(annotations),
                }),
            )
            count += 1

    return count
