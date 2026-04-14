"""Run the Stockfish-based puzzle extractor over a user's recent games."""
from __future__ import annotations

import asyncio
import time

import typer

from .. import db
from ..engine.puzzle_extractor import extract_puzzles_for_user
from ..engine.stockfish import StockfishPool


app = typer.Typer(add_completion=False)


@app.command()
def main(
    user_id: int = typer.Option(...),
    limit_games: int = typer.Option(50),
    stockfish_workers: int = typer.Option(2),
) -> None:
    async def run() -> None:
        await db.init_pool()
        pool = StockfishPool(size=stockfish_workers)
        t0 = time.monotonic()
        # Rough estimate: 40 moves/game * ~0.5s Stockfish analysis = ~20s/game
        # with 2 workers -> ~10s/game effective. 50 games -> ~8 min.
        est_sec = int(limit_games * 10 / max(stockfish_workers, 1))
        typer.echo(
            f"[puzzles] extracting from up to {limit_games} games "
            f"(workers={stockfish_workers}). ETA ~{est_sec//60}m{est_sec%60:02d}s."
        )
        try:
            n = await extract_puzzles_for_user(user_id, limit_games=limit_games, pool=pool)
            typer.echo(
                f"[puzzles] extracted {n} puzzles for user {user_id} "
                f"in {time.monotonic()-t0:.0f}s"
            )
        finally:
            await pool.close()
            await db.close_pool()

    asyncio.run(run())


if __name__ == "__main__":
    app()
