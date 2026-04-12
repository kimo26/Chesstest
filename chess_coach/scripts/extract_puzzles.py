"""Run the Stockfish-based puzzle extractor over a user's recent games."""
from __future__ import annotations

import asyncio

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
        try:
            n = await extract_puzzles_for_user(user_id, limit_games=limit_games, pool=pool)
            typer.echo(f"Extracted {n} puzzles for user {user_id}")
        finally:
            await pool.close()
            await db.close_pool()

    asyncio.run(run())


if __name__ == "__main__":
    app()
