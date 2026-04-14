"""Fine-tune a maia-individual model on a single opponent's game history."""
from __future__ import annotations

import asyncio
import time
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
        t0 = time.monotonic()
        # On an RTX 5090, Maia-1 fine-tune ~= 8-12 steps/sec at batch 256.
        # 4000 steps -> ~6-8 min. Plus ~1-2 min data prep. Expect ~10 min.
        est_sec = int(steps / 10) + 120
        typer.echo(
            f"[maia-train] {opponent}: {steps} steps, batch {batch_size}. "
            f"ETA ~{est_sec//60}m{est_sec%60:02d}s on a modern GPU."
        )
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
                f"[maia-train] done in {time.monotonic()-t0:.0f}s. "
                f"games used : {artifacts.games_used}\n"
                f"             weights    : {artifacts.weights_path}"
            )
        finally:
            await db.close_pool()

    asyncio.run(run())


if __name__ == "__main__":
    app()
