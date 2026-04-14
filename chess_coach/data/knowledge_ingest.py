"""Broader knowledge base ingestion pipelines.

Ingests chess theory content beyond just opening descriptions:

1. **Wikibooks Chess** — strategy, middlegame, endgame chapters from
   ``en.wikibooks.org/wiki/Chess``.
2. **Lichess puzzle DB** — rated community puzzles from the Lichess
   open database.
3. **Annotated PGN files** — parse ``.pgn`` files with comments and
   store annotated positions as RAG documents.
4. **Chess theory PDFs** — e.g. "Chess: The Words of Wisdom" — downloaded
   or loaded from disk, text-extracted with pypdf, chunked, embedded.

All text content is chunked, embedded, and stored in ``rag_documents``
so the RAG retriever can surface strategy/endgame/middlegame knowledge
during coach chat sessions.
"""
from __future__ import annotations

import csv
import io
import json
import re
import time
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


# ── PDF ingestion (chess theory books) ──────────────────────────────────

# Preset URLs for chess theory PDFs we know are public-domain / redistributable.
PDF_PRESETS: dict[str, dict[str, str]] = {
    "chess-wisdom": {
        "url": (
            "https://ia803100.us.archive.org/24/items/ChessMazes2gnv64/"
            "Chess%20Words%20of%20Wisdom%20-%20The%20Principles%2C%20Methods"
            "%20and%20Essential%20Knowledge%20of%20Chess.pdf"
        ),
        "title": "Chess: The Words of Wisdom",
        "author": "Mike Henebry",
        "chunk_level": "strategy",
    },
}


def _extract_pdf_text(pdf_bytes: bytes) -> list[tuple[int, str]]:
    """Return ``[(page_num, text), ...]`` for every page of a PDF.

    Pages with <= 50 visible characters are dropped (empty / cover pages).
    """
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages: list[tuple[int, str]] = []
    for i, page in enumerate(reader.pages):
        try:
            txt = page.extract_text() or ""
        except Exception:
            txt = ""
        txt = re.sub(r"[ \t]+", " ", txt).strip()
        if len(txt) >= 50:
            pages.append((i + 1, txt))
    return pages


def _group_pages(pages: list[tuple[int, str]], target_chars: int = 2500) -> list[dict[str, Any]]:
    """Group contiguous pages into larger chunks ~``target_chars`` long."""
    out: list[dict[str, Any]] = []
    buf: list[str] = []
    start_page = pages[0][0] if pages else 1
    size = 0
    last_page = start_page
    for pnum, text in pages:
        if size + len(text) > target_chars and buf:
            out.append({
                "start": start_page,
                "end": last_page,
                "text": "\n\n".join(buf),
            })
            buf = []
            size = 0
            start_page = pnum
        buf.append(text)
        size += len(text)
        last_page = pnum
    if buf:
        out.append({
            "start": start_page,
            "end": last_page,
            "text": "\n\n".join(buf),
        })
    return out


async def _download_pdf(url: str, *, cache_dir: Path | None = None) -> bytes:
    """Download (or load from cache) a PDF file."""
    if cache_dir is None:
        cache_dir = Path("./data/pdfs")
    cache_dir.mkdir(parents=True, exist_ok=True)
    # Hashed filename so URL changes don't hit stale cache.
    import hashlib
    h = hashlib.sha1(url.encode()).hexdigest()[:16]
    cache_path = cache_dir / f"{h}.pdf"
    if cache_path.exists():
        return cache_path.read_bytes()
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        resp = await client.get(url, headers={"User-Agent": settings.user_agent})
        resp.raise_for_status()
        cache_path.write_bytes(resp.content)
        return resp.content


async def ingest_pdf(
    source: str,
    *,
    title: str | None = None,
    author: str | None = None,
    chunk_level: str = "strategy",
    max_chunks: int | None = None,
) -> int:
    """Ingest a PDF into the RAG corpus.

    ``source`` may be a URL (http/https) or a local file path. Each chunk
    of ~2500 chars becomes one ``rag_documents`` row with source_type
    'pdf_book' so it shows up in retrieval alongside Wikipedia/Wikibooks.

    Returns number of chunks inserted. Prints progress + ETA.
    """
    t0 = time.monotonic()
    if source.startswith(("http://", "https://")):
        print(f"[pdf] downloading {source}…")
        pdf_bytes = await _download_pdf(source)
    else:
        p = Path(source)
        if not p.exists():
            raise FileNotFoundError(f"PDF not found at {source}")
        pdf_bytes = p.read_bytes()

    print(f"[pdf] extracting text ({len(pdf_bytes)/1e6:.1f} MB)…")
    pages = _extract_pdf_text(pdf_bytes)
    if not pages:
        print("[pdf] no extractable text; skipping.")
        return 0

    chunks = _group_pages(pages)
    if max_chunks:
        chunks = chunks[:max_chunks]
    print(
        f"[pdf] {len(pages)} pages -> {len(chunks)} chunks. "
        f"Embedding (ETA ~{len(chunks)*0.4:.0f}s on local GPU)…"
    )

    # Short book-level title + attribution.
    display_title = title or Path(source).stem
    attribution = f"\n\nSource: {display_title}" + (f" by {author}" if author else "") + "."

    client = get_client()
    inserted = 0
    for i, chunk in enumerate(chunks, 1):
        content = chunk["text"] + attribution
        # Guard against embedding overflow.
        if len(content) > 6000:
            content = content[:6000]
        embedding = await client.embed_one(content)
        await db.execute(
            """
            INSERT INTO rag_documents (
                opening_node_id, chunk_level, title, content, embedding,
                metadata, source_type, source_model
            ) VALUES (NULL, $1, $2, $3, $4::vector, $5, 'pdf_book', NULL)
            """,
            chunk_level,
            f"{display_title} (pp. {chunk['start']}-{chunk['end']})",
            content,
            db.vector_literal(embedding),
            json.dumps({
                "source_url": source if source.startswith("http") else None,
                "source_file": source if not source.startswith("http") else None,
                "author": author,
                "page_start": chunk["start"],
                "page_end": chunk["end"],
                "chunk_level": chunk_level,
            }),
        )
        inserted += 1
        if i % 10 == 0 or i == len(chunks):
            elapsed = time.monotonic() - t0
            eta = elapsed / i * (len(chunks) - i)
            print(f"[pdf] {i}/{len(chunks)} chunks embedded (ETA {eta:.0f}s)")

    print(f"[pdf] done: {inserted} chunks in {time.monotonic()-t0:.0f}s")
    return inserted


async def ingest_pdf_preset(name: str) -> int:
    """Ingest a PDF from the built-in preset list (e.g. 'chess-wisdom')."""
    preset = PDF_PRESETS.get(name)
    if preset is None:
        raise ValueError(
            f"Unknown PDF preset '{name}'. Options: {list(PDF_PRESETS)}"
        )
    return await ingest_pdf(
        preset["url"],
        title=preset.get("title"),
        author=preset.get("author"),
        chunk_level=preset.get("chunk_level", "strategy"),
    )


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
