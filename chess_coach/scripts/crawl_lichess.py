"""BFS-crawl the Lichess opening explorer to fill ``opening_nodes``."""
from __future__ import annotations

import asyncio

import typer

from .. import db
from ..data.lichess_explorer import crawl_tree


app = typer.Typer(add_completion=False)


@app.command()
def main(
    max_depth: int = typer.Option(12, help="Maximum ply depth to crawl"),
    min_games: int = typer.Option(1000, help="Prune moves with fewer games"),
    max_children: int = typer.Option(8, help="Expand top-K moves per node"),
    delay: float = typer.Option(0.5, help="Delay between requests (seconds)"),
) -> None:
    async def run() -> None:
        await db.init_pool()
        try:
            n = await crawl_tree(
                max_depth=max_depth,
                min_games=min_games,
                max_children_per_node=max_children,
                request_delay_s=delay,
            )
            typer.echo(f"Crawled {n} positions")
        finally:
            await db.close_pool()

    asyncio.run(run())


if __name__ == "__main__":
    app()
