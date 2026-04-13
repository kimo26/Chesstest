#!/usr/bin/env python3
"""CLI script to ingest broader chess knowledge into the RAG corpus.

Usage:
    # Ingest Wikibooks Chess pages (strategy, endgames, tactics, etc.)
    python -m chess_coach.scripts.ingest_knowledge wikibooks

    # Import Lichess puzzles from CSV (download + decompress first)
    python -m chess_coach.scripts.ingest_knowledge lichess-puzzles /path/to/lichess_db_puzzle.csv

    # Ingest annotated PGN files
    python -m chess_coach.scripts.ingest_knowledge annotated-pgn /path/to/games.pgn

    # Run all available ingestion pipelines
    python -m chess_coach.scripts.ingest_knowledge all
"""
from __future__ import annotations

import asyncio
import sys

from ..data.knowledge_ingest import (
    ingest_annotated_pgn,
    ingest_lichess_puzzles,
    ingest_wikibooks,
)
from .. import db


async def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    await db.init_pool()

    try:
        if cmd == "wikibooks":
            print("Ingesting Wikibooks Chess pages...")
            count = await ingest_wikibooks()
            print(f"Ingested {count} chunks from Wikibooks.")

        elif cmd == "lichess-puzzles":
            if len(sys.argv) < 3:
                print("Usage: ... lichess-puzzles /path/to/lichess_db_puzzle.csv")
                sys.exit(1)
            csv_path = sys.argv[2]
            limit = int(sys.argv[3]) if len(sys.argv) > 3 else 50000
            print(f"Importing Lichess puzzles from {csv_path} (limit={limit})...")
            count = await ingest_lichess_puzzles(csv_path, limit=limit)
            print(f"Imported {count} puzzles from Lichess.")

        elif cmd == "annotated-pgn":
            if len(sys.argv) < 3:
                print("Usage: ... annotated-pgn /path/to/games.pgn")
                sys.exit(1)
            pgn_path = sys.argv[2]
            limit = int(sys.argv[3]) if len(sys.argv) > 3 else 1000
            print(f"Ingesting annotated PGN from {pgn_path} (limit={limit})...")
            count = await ingest_annotated_pgn(pgn_path, limit=limit)
            print(f"Ingested {count} annotated games.")

        elif cmd == "all":
            print("Running all ingestion pipelines...\n")
            print("1. Wikibooks Chess...")
            wb = await ingest_wikibooks()
            print(f"   -> {wb} chunks\n")

            if len(sys.argv) > 2:
                csv_path = sys.argv[2]
                print(f"2. Lichess puzzles from {csv_path}...")
                lp = await ingest_lichess_puzzles(csv_path)
                print(f"   -> {lp} puzzles\n")

            if len(sys.argv) > 3:
                pgn_path = sys.argv[3]
                print(f"3. Annotated PGN from {pgn_path}...")
                ap = await ingest_annotated_pgn(pgn_path)
                print(f"   -> {ap} games\n")

            print("Done.")
        else:
            print(f"Unknown command: {cmd}")
            print(__doc__)
            sys.exit(1)
    finally:
        await db.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
