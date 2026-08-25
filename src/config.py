from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    OPENSQL_HOST: str = "localhost"
    OPENSQL_PORT: int = 5432
    OPENSQL_DB: str = "doc_search"
    OPENSQL_USER: str = "app_user"
    OPENSQL_PASSWORD: str
    OPENSQL_POOL_MIN: int = 2
    OPENSQL_POOL_MAX: int = 10
    OPENSQL_COMMAND_TIMEOUT_SECONDS: float = 30.0

    EMBEDDING_API_PROVIDER: str = "ollama"
    EMBEDDING_BASE_URL: str = "http://127.0.0.1:18080"
    EMBEDDING_MODEL: str = "qwen3-embedding:0.6b"
    EMBEDDING_DIMENSIONS: int = 1024
    EMBEDDING_TIMEOUT_SECONDS: float = 60.0
    EMBEDDING_KEEP_ALIVE: str = "5m"
    EMBEDDING_QUERY_INSTRUCTION: str = (
        "Given a Korean enterprise document search query, retrieve relevant passages "
        "that answer the query"
    )

    CHUNK_SIZE: int = 512
    CHUNK_OVERLAP: int = 50
    EMBEDDING_BATCH_SIZE: int = 100
    MAX_RETRIES: int = 3
    WORKER_LOCK_TIMEOUT_MINUTES: int = 10
    WORKER_POLL_INTERVAL_SECONDS: int = 5
    MAX_FILE_SIZE_MB: int = 100
    UPLOAD_STREAM_CHUNK_SIZE_BYTES: int = 1024 * 1024

    SEARCH_DEFAULT_TOP_K: int = 5
    SEARCH_MAX_TOP_K: int = 100
    SEARCH_MIN_SIMILARITY: float = 0.0

    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    API_RELOAD: bool = True
    MCP_SERVER_HOST: str = "0.0.0.0"
    MCP_SERVER_PORT: int = 8080

    LOG_LEVEL: str = "INFO"
    UPLOAD_DIR: str = "./uploads"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
