"""Refresh the Wikipedia-sourced opening corpus.

Queries Wikidata for every item that is an instance of "chess opening",
fetches the English Wikipedia extract for each, and upserts into
``wiki_openings``. This is the source of truth for every opening
description the RAG system serves.
"""
from __future__ import annotations

import asyncio
import time

import typer

from .. import db
from ..data.wikidata import refresh_wiki_openings


app = typer.Typer(add_completion=False)


@app.command()
def main() -> None:
    async def run() -> None:
        await db.init_pool()
        t0 = time.monotonic()
        # Wikidata SPARQL returns ~400 chess opening items; we then fetch
        # each Wikipedia extract. With 5 concurrent workers and MediaWiki's
        # ~200ms/request, total is ~30-90 seconds.
        typer.echo("[wiki] refreshing Wikipedia opening corpus. ETA ~1-2 min.")
        try:
            n = await refresh_wiki_openings()
            typer.echo(f"[wiki] refreshed {n} articles in {time.monotonic()-t0:.0f}s")
        finally:
            await db.close_pool()

    asyncio.run(run())


if __name__ == "__main__":
    app()
