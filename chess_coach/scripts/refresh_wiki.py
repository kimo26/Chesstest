"""Refresh the Wikipedia-sourced opening corpus.

Queries Wikidata for every item that is an instance of "chess opening",
fetches the English Wikipedia extract for each, and upserts into
``wiki_openings``. This is the source of truth for every opening
description the RAG system serves.
"""
from __future__ import annotations

import asyncio

import typer

from .. import db
from ..data.wikidata import refresh_wiki_openings


app = typer.Typer(add_completion=False)


@app.command()
def main() -> None:
    async def run() -> None:
        await db.init_pool()
        try:
            n = await refresh_wiki_openings()
            typer.echo(f"Refreshed {n} Wikipedia articles")
        finally:
            await db.close_pool()

    asyncio.run(run())


if __name__ == "__main__":
    app()
