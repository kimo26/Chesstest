"""Async Postgres pool + a few ergonomic helpers.

asyncpg is used directly; for pgvector we encode embeddings as the
``'[1.0,2.0,...]'`` text literal that pgvector accepts in every parameterised
query. That avoids depending on the ``pgvector.asyncpg`` extension module
while still giving us full type safety.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Iterable, Sequence

import asyncpg

from .config import settings

_pool: asyncpg.Pool | None = None


async def init_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=settings.database_url,
            min_size=settings.db_pool_min,
            max_size=settings.db_pool_max,
            command_timeout=60,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool has not been initialised — call init_pool() first")
    return _pool


@asynccontextmanager
async def acquire() -> AsyncIterator[asyncpg.Connection]:
    pool = get_pool()
    async with pool.acquire() as conn:
        yield conn


async def fetch(sql: str, *args: Any) -> list[asyncpg.Record]:
    async with acquire() as conn:
        return await conn.fetch(sql, *args)


async def fetchrow(sql: str, *args: Any) -> asyncpg.Record | None:
    async with acquire() as conn:
        return await conn.fetchrow(sql, *args)


async def fetchval(sql: str, *args: Any) -> Any:
    async with acquire() as conn:
        return await conn.fetchval(sql, *args)


async def execute(sql: str, *args: Any) -> str:
    async with acquire() as conn:
        return await conn.execute(sql, *args)


async def executemany(sql: str, rows: Iterable[Sequence[Any]]) -> None:
    async with acquire() as conn:
        await conn.executemany(sql, list(rows))


# --- pgvector helpers ------------------------------------------------------

def vector_literal(vec: Sequence[float]) -> str:
    """Render a Python sequence as the pgvector text literal."""
    return "[" + ",".join(f"{float(x):.6f}" for x in vec) + "]"
