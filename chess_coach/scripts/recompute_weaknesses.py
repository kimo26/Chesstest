"""Rebuild ``user_weaknesses`` from a user's imported games."""
from __future__ import annotations

import asyncio

import typer

from .. import db
from ..analysis.weakness import recompute_weaknesses, repertoire_gaps


app = typer.Typer(add_completion=False)


@app.command()
def main(
    user_id: int = typer.Option(...),
    show: int = typer.Option(10, help="How many worst openings to print"),
) -> None:
    async def run() -> None:
        await db.init_pool()
        try:
            n = await recompute_weaknesses(user_id)
            typer.echo(f"Updated {n} weakness rows")
            gaps = await repertoire_gaps(user_id, limit=show)
            for g in gaps:
                typer.echo(
                    f"  [{g['eco_code']}] {g['opening_name']} ({g['color']}): "
                    f"{g['games_played']}g, win_rate={g['win_rate']:.2f}, "
                    f"score={g['weakness_score']:.2f} ({g['weakness_type']})"
                )
        finally:
            await db.close_pool()

    asyncio.run(run())


if __name__ == "__main__":
    app()
