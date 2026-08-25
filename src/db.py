from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

import asyncpg

from src.config import settings


class Database:
    """Lazy asyncpg connection pool shared by API, Worker and MCP."""

    def __init__(self) -> None:
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self) -> None:
        if self.pool is not None:
            return

        self.pool = await asyncpg.create_pool(
            host=settings.OPENSQL_HOST,
            port=settings.OPENSQL_PORT,
            user=settings.OPENSQL_USER,
            password=settings.OPENSQL_PASSWORD,
            database=settings.OPENSQL_DB,
            min_size=settings.OPENSQL_POOL_MIN,
            max_size=settings.OPENSQL_POOL_MAX,
            command_timeout=settings.OPENSQL_COMMAND_TIMEOUT_SECONDS,
            server_settings={"application_name": "opensql-doc-search"},
        )

    async def disconnect(self) -> None:
        if self.pool is None:
            return

        await self.pool.close()
        self.pool = None

    async def get_connection(self) -> Any:
        """Return asyncpg's acquisition context for legacy callers."""
        if self.pool is None:
            await self.connect()
        if self.pool is None:  # pragma: no cover - defensive type narrowing
            raise RuntimeError("database pool was not initialized")
        return self.pool.acquire()

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[asyncpg.Connection]:
        acquisition = await self.get_connection()
        async with acquisition as connection:
            yield connection


db = Database()
