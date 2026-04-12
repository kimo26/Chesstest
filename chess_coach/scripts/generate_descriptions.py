"""Hydrate opening_nodes.description from the Wikipedia corpus, then chunk
and embed the results into ``rag_documents``.

Assumes ``scripts.refresh_wiki`` has already populated ``wiki_openings``.
"""
from __future__ import annotations

import asyncio

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
        try:
            n = await generate_missing(
                limit=limit,
                min_total_games=min_total_games,
                concurrency=concurrency,
            )
            typer.echo(f"Hydrated {n} opening nodes from Wikipedia")
        finally:
            await get_client().aclose()
            await db.close_pool()

    asyncio.run(run())


if __name__ == "__main__":
    app()
