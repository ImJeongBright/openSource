import asyncpg
from typing import Optional
from src.config import settings

class Database:
    def __init__(self):
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self):
        """커넥션 풀 초기화"""
        if self.pool is None:
            self.pool = await asyncpg.create_pool(
                host=settings.OPENSQL_HOST,
                port=settings.OPENSQL_PORT,
                user=settings.OPENSQL_USER,
                password=settings.OPENSQL_PASSWORD,
                database=settings.OPENSQL_DB,
                min_size=settings.OPENSQL_POOL_MIN,
                max_size=settings.OPENSQL_POOL_MAX,
            )

    async def disconnect(self):
        """커넥션 풀 종료"""
        if self.pool:
            await self.pool.close()
            self.pool = None

    async def get_connection(self):
        """단일 커넥션 획득 (반드시 컨텍스트 매니저로 사용)"""
        if self.pool is None:
            await self.connect()
        return self.pool.acquire()

# 싱글톤 인스턴스
db = Database()
