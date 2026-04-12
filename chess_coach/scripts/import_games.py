"""Import a user's (or opponent's) Chess.com game history."""
from __future__ import annotations

import asyncio

import typer

from .. import db
from ..data.chesscom_importer import import_opponent_games, import_user_games


app = typer.Typer(add_completion=False)


@app.command("user")
def user(
    user_id: int = typer.Option(..., help="DB user id"),
    username: str = typer.Option(..., help="Chess.com username"),
    months_back: int = typer.Option(12),
) -> None:
    async def run() -> None:
        await db.init_pool()
        try:
            n = await import_user_games(user_id, username, months_back=months_back)
            typer.echo(f"Imported {n} games for user {user_id}")
        finally:
            await db.close_pool()

    asyncio.run(run())


@app.command("opponent")
def opponent(
    opponent_name: str = typer.Option(..., help="Chess.com username of opponent"),
    months_back: int = typer.Option(24),
) -> None:
    async def run() -> None:
        await db.init_pool()
        try:
            n = await import_opponent_games(opponent_name, months_back=months_back)
            typer.echo(f"Imported {n} opponent games for {opponent_name}")
        finally:
            await db.close_pool()

    asyncio.run(run())


if __name__ == "__main__":
    app()
