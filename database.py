"""Shared request connection and transaction boundary for authentication and routes."""
from collections.abc import AsyncIterator
from typing import Annotated

import asyncpg
from fastapi import Depends, HTTPException, Request, status


async def get_connection(request: Request) -> AsyncIterator[asyncpg.Connection]:
    pool: asyncpg.Pool | None = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is not ready",
        )
    async with pool.acquire() as connection:
        # Serialize domain writes across workers, including generation and deactivation.
        # READ COMMITTED reads after the lock see the preceding writer's committed state.
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not request.url.path.endswith("/auth/login"):
            async with connection.transaction():
                await connection.execute("SELECT pg_advisory_xact_lock(20261005)")
                yield connection
        else:
            yield connection


DbConnection = Annotated[asyncpg.Connection, Depends(get_connection, scope="function")]
