"""Fine-tune a maia-individual model on a single opponent's game history."""
from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from .. import db
from ..maia.individual_trainer import train_individual_model


app = typer.Typer(add_completion=False)


@app.command()
def main(
    opponent: str = typer.Option(..., help="Chess.com username to fine-tune on"),
    base_weights: Path = typer.Option(None, help="Path to base Maia-1 weights"),
    steps: int = typer.Option(4000),
    batch_size: int = typer.Option(256),
    learning_rate: float = typer.Option(1e-4),
    min_games: int = typer.Option(20),
) -> None:
    async def run() -> None:
        await db.init_pool()
        try:
            artifacts = await train_individual_model(
                opponent=opponent,
                base_weights=base_weights,
                steps=steps,
                batch_size=batch_size,
                learning_rate=learning_rate,
                min_games=min_games,
            )
            typer.echo(
                f"Trained maia-individual for {opponent}:\n"
                f"  games used : {artifacts.games_used}\n"
                f"  weights    : {artifacts.weights_path}"
            )
        finally:
            await db.close_pool()

    asyncio.run(run())


if __name__ == "__main__":
    app()
