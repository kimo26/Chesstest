"""Load the lichess-org/chess-openings ECO TSVs into ``opening_nodes``."""
from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from .. import db
from ..data.eco import load_eco_tsv, upsert_eco_entries, link_parents


app = typer.Typer(add_completion=False)


@app.command()
def main(
    directory: Path = typer.Option(
        Path("./data/chess-openings"),
        help="Directory containing a.tsv..e.tsv",
    ),
) -> None:
    """Load every ECO TSV and link parent/child relationships."""

    async def run() -> None:
        await db.init_pool()
        import time
        t0 = time.monotonic()
        typer.echo("[eco] loading ECO TSVs. ETA <30s.")
        try:
            entries = load_eco_tsv(directory)
            typer.echo(f"[eco] loaded {len(entries)} entries from {directory}")
            inserted = await upsert_eco_entries(entries)
            typer.echo(f"[eco] upserted {inserted} opening_nodes")
            linked = await link_parents()
            typer.echo(
                f"[eco] linked {linked} parent/child relationships "
                f"in {time.monotonic()-t0:.1f}s"
            )
        finally:
            await db.close_pool()

    asyncio.run(run())


if __name__ == "__main__":
    app()
