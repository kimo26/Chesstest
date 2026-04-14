"""BFS-crawl the Lichess opening explorer to fill ``opening_nodes``.

Rough ETA: ``max_children ** max_depth / branching_cut`` API calls at
``delay`` seconds each. Defaults (depth=12, children=8, min_games=1000,
delay=0.5s) usually converge in ~15-30 minutes because the min_games
filter prunes the tree aggressively.
"""
from __future__ import annotations

import asyncio
import time

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
        t0 = time.monotonic()
        typer.echo(
            f"[lichess] crawling (depth={max_depth}, min_games={min_games}, "
            f"children={max_children}, delay={delay}s). "
            f"ETA: ~15-30 min with defaults."
        )
        try:
            n = await crawl_tree(
                max_depth=max_depth,
                min_games=min_games,
                max_children_per_node=max_children,
                request_delay_s=delay,
            )
            typer.echo(f"[lichess] crawled {n} positions in {time.monotonic()-t0:.0f}s")
        finally:
            await db.close_pool()

    asyncio.run(run())


if __name__ == "__main__":
    app()
