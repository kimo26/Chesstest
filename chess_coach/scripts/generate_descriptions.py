"""Hydrate opening_nodes.description from the Wikipedia corpus, then chunk
and embed the results into ``rag_documents``.

Assumes ``scripts.refresh_wiki`` has already populated ``wiki_openings``.
"""
from __future__ import annotations

import asyncio
import time

import typer

from .. import db
from ..llm import get_client
from ..rag.generator import generate_missing


app = typer.Typer(add_completion=False)


@app.command()
def main(
    limit: int | None = typer.Option(None, help="Cap the number of nodes processed"),
    min_total_games: int = typer.Option(500, help="Skip rarely-played nodes"),
    concurrency: int = typer.Option(4, help="Parallel workers (Ollama requests)"),
) -> None:
    async def run() -> None:
        await db.init_pool()
        t0 = time.monotonic()
        # Each node requires: Wikipedia lookup (~0.5s) + metadata LLM call
        # (~3s on 14b) + ~1-2 embedding calls (~0.5s each). With
        # concurrency=4 expect ~1.5s/node. 2000 nodes -> ~50 min.
        est_nodes = limit or 2000
        est_sec = int(est_nodes * 1.5 / max(concurrency, 1))
        typer.echo(
            f"[descriptions] hydrating up to {est_nodes} nodes "
            f"(concurrency={concurrency}). ETA ~{est_sec//60}m{est_sec%60:02d}s."
        )
        try:
            n = await generate_missing(
                limit=limit,
                min_total_games=min_total_games,
                concurrency=concurrency,
            )
            typer.echo(
                f"[descriptions] hydrated {n} opening nodes in {time.monotonic()-t0:.0f}s"
            )
        finally:
            await get_client().aclose()
            await db.close_pool()

    asyncio.run(run())


if __name__ == "__main__":
    app()
