from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    # DB 접속 정보
    OPENSQL_HOST: str = "localhost"
    OPENSQL_PORT: int = 5432
    OPENSQL_DB: str = "doc_search"
    OPENSQL_USER: str = "app_user"
    OPENSQL_PASSWORD: str
    OPENSQL_POOL_MIN: int = 2
    OPENSQL_POOL_MAX: int = 10
    
    # 임베딩 API
    EMBEDDING_API_PROVIDER: str = "openai"
    EMBEDDING_API_KEY: str
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    EMBEDDING_DIMENSIONS: int = 1536
    
    # 파이프라인 설정
    CHUNK_SIZE: int = 512
    CHUNK_OVERLAP: int = 50
    EMBEDDING_BATCH_SIZE: int = 100
    MAX_RETRIES: int = 3
    WORKER_LOCK_TIMEOUT_MINUTES: int = 10
    WORKER_POLL_INTERVAL_SECONDS: int = 5
    MAX_FILE_SIZE_MB: int = 100
    
    # 서버 설정
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    API_RELOAD: bool = True
    MCP_SERVER_HOST: str = "0.0.0.0"
    MCP_SERVER_PORT: int = 8080
    
    # 시스템 설정
    LOG_LEVEL: str = "INFO"
    UPLOAD_DIR: str = "./uploads"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

# 전역 설정 객체
settings = Settings()
