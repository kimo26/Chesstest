"""Create a local user row."""
from __future__ import annotations

import asyncio

import typer

from .. import db


app = typer.Typer(add_completion=False)


@app.command()
def main(
    username: str = typer.Option(...),
    email: str | None = typer.Option(None),
    chess_com: str | None = typer.Option(None, "--chess-com"),
    lichess: str | None = typer.Option(None),
) -> None:
    async def run() -> None:
        await db.init_pool()
        try:
            row = await db.fetchrow(
                """
                INSERT INTO users (username, email, chess_com_user, lichess_user)
                VALUES ($1,$2,$3,$4)
                ON CONFLICT (username) DO UPDATE SET
                    email = COALESCE(EXCLUDED.email, users.email),
                    chess_com_user = COALESCE(EXCLUDED.chess_com_user, users.chess_com_user),
                    lichess_user = COALESCE(EXCLUDED.lichess_user, users.lichess_user)
                RETURNING id
                """,
                username, email, chess_com, lichess,
            )
            typer.echo(f"user_id={row['id']}")
        finally:
            await db.close_pool()

    asyncio.run(run())


if __name__ == "__main__":
    app()
