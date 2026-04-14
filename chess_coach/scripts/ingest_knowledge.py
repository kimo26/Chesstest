#!/usr/bin/env python3
"""CLI script to ingest broader chess knowledge into the RAG corpus.

Usage:
    # Ingest Wikibooks Chess pages (strategy, endgames, tactics, etc.)
    python -m chess_coach.scripts.ingest_knowledge wikibooks

    # Import Lichess puzzles from CSV (download + decompress first)
    python -m chess_coach.scripts.ingest_knowledge lichess-puzzles /path/to/lichess_db_puzzle.csv

    # Ingest annotated PGN files
    python -m chess_coach.scripts.ingest_knowledge annotated-pgn /path/to/games.pgn

    # Ingest a PDF (local file or URL)
    python -m chess_coach.scripts.ingest_knowledge pdf /path/or/https://url.pdf

    # Ingest a preset PDF (e.g. chess-wisdom — Chess: The Words of Wisdom)
    python -m chess_coach.scripts.ingest_knowledge pdf-preset chess-wisdom

    # Run all available ingestion pipelines (incl. chess-wisdom preset)
    python -m chess_coach.scripts.ingest_knowledge all

Rough time estimates (RTX 5090 + 1 Gbps network):
    - wikibooks         : ~2 min   (~150 chunks)
    - lichess-puzzles   : ~5 min   (50k rows, no embedding)
    - annotated-pgn     : ~1 min / 1k games
    - pdf-preset        : ~5-10 min per 300-page book
    - all (w/ preset)   : ~10-15 min end-to-end
"""
from __future__ import annotations

import asyncio
import sys
import time

from ..data.knowledge_ingest import (
    ingest_annotated_pgn,
    ingest_lichess_puzzles,
    ingest_pdf,
    ingest_pdf_preset,
    ingest_wikibooks,
)
from .. import db


def _fmt_secs(s: float) -> str:
    if s < 60:
        return f"{s:.0f}s"
    m, s = divmod(s, 60)
    return f"{int(m)}m{int(s):02d}s"


async def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    await db.init_pool()
    t0 = time.monotonic()

    try:
        if cmd == "wikibooks":
            print("[wikibooks] ingesting ~17 pages (ETA ~2 min)…")
            count = await ingest_wikibooks()
            print(f"[wikibooks] {count} chunks in {_fmt_secs(time.monotonic()-t0)}")

        elif cmd == "lichess-puzzles":
            if len(sys.argv) < 3:
                print("Usage: ... lichess-puzzles /path/to/lichess_db_puzzle.csv")
                sys.exit(1)
            csv_path = sys.argv[2]
            limit = int(sys.argv[3]) if len(sys.argv) > 3 else 50000
            print(f"[lichess] importing up to {limit} puzzles (ETA ~{limit//10000} min)…")
            count = await ingest_lichess_puzzles(csv_path, limit=limit)
            print(f"[lichess] {count} puzzles in {_fmt_secs(time.monotonic()-t0)}")

        elif cmd == "annotated-pgn":
            if len(sys.argv) < 3:
                print("Usage: ... annotated-pgn /path/to/games.pgn")
                sys.exit(1)
            pgn_path = sys.argv[2]
            limit = int(sys.argv[3]) if len(sys.argv) > 3 else 1000
            print(f"[pgn] ingesting up to {limit} annotated games (ETA ~{limit//1000} min)…")
            count = await ingest_annotated_pgn(pgn_path, limit=limit)
            print(f"[pgn] {count} games in {_fmt_secs(time.monotonic()-t0)}")

        elif cmd == "pdf":
            if len(sys.argv) < 3:
                print("Usage: ... pdf /path/or/url.pdf [--title 'Book Title'] [--author 'Name']")
                sys.exit(1)
            source = sys.argv[2]
            # Simple --title/--author flag parsing.
            title = None
            author = None
            for i, arg in enumerate(sys.argv[3:], 3):
                if arg == "--title" and i + 1 < len(sys.argv):
                    title = sys.argv[i + 1]
                elif arg == "--author" and i + 1 < len(sys.argv):
                    author = sys.argv[i + 1]
            count = await ingest_pdf(source, title=title, author=author)
            print(f"[pdf] {count} chunks in {_fmt_secs(time.monotonic()-t0)}")

        elif cmd == "pdf-preset":
            if len(sys.argv) < 3:
                print("Usage: ... pdf-preset <name>   (known: chess-wisdom)")
                sys.exit(1)
            name = sys.argv[2]
            count = await ingest_pdf_preset(name)
            print(f"[pdf-preset:{name}] {count} chunks in {_fmt_secs(time.monotonic()-t0)}")

        elif cmd == "all":
            print("[all] running every available pipeline.\n")

            print("1/4 Wikibooks Chess (ETA ~2 min)…")
            t = time.monotonic()
            wb = await ingest_wikibooks()
            print(f"    -> {wb} chunks in {_fmt_secs(time.monotonic()-t)}\n")

            print("2/4 PDF preset: Chess: The Words of Wisdom (ETA ~8 min)…")
            t = time.monotonic()
            try:
                pd = await ingest_pdf_preset("chess-wisdom")
                print(f"    -> {pd} chunks in {_fmt_secs(time.monotonic()-t)}\n")
            except Exception as exc:  # network errors shouldn't kill the whole run
                print(f"    ! skipped ({exc})\n")

            # Optional positional args for lichess CSV and annotated PGN paths.
            csv_path = sys.argv[2] if len(sys.argv) > 2 else None
            pgn_path = sys.argv[3] if len(sys.argv) > 3 else None

            if csv_path:
                print(f"3/4 Lichess puzzles from {csv_path} (ETA ~5 min)…")
                t = time.monotonic()
                lp = await ingest_lichess_puzzles(csv_path)
                print(f"    -> {lp} puzzles in {_fmt_secs(time.monotonic()-t)}\n")

            if pgn_path:
                print(f"4/4 Annotated PGN from {pgn_path} (ETA ~1 min / 1k games)…")
                t = time.monotonic()
                ap = await ingest_annotated_pgn(pgn_path)
                print(f"    -> {ap} games in {_fmt_secs(time.monotonic()-t)}\n")

            print(f"[all] done in {_fmt_secs(time.monotonic()-t0)}")
        else:
            print(f"Unknown command: {cmd}")
            print(__doc__)
            sys.exit(1)
    finally:
        await db.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
