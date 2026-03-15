from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderName = Literal["openai", "gemini", "ollama"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = Field(default="backend-rag-multipurpose")
    app_host: str = Field(default="0.0.0.0")
    app_port: int = Field(default=8000)
    log_level: str = Field(default="INFO")

    postgres_dsn: str = Field(
        default="postgresql://postgres:postgres@localhost:5432/ragdb"
    )
    postgres_min_pool_size: int = Field(default=1)
    postgres_max_pool_size: int = Field(default=10)

    redis_url: str = Field(default="redis://localhost:6379/0")
    redis_max_connections: int = Field(default=10)

    openai_enabled: bool = Field(default=False)
    openai_api_key: str | None = Field(default=None)

    gemini_enabled: bool = Field(default=False)
    gemini_api_key: str | None = Field(default=None)

    ollama_enabled: bool = Field(default=True)
    ollama_base_url: str = Field(default="http://localhost:11434")
    ollama_health_timeout_seconds: float = Field(default=3.0)

    default_llm_provider: ProviderName = Field(default="ollama")
    default_llm_model: str = Field(default="llama3.2")
    default_embedding_provider: ProviderName = Field(default="ollama")
    default_embedding_model: str = Field(default="qwen3-embedding")
    canonical_embedding_dimension: int = Field(default=4096)
    chunk_size: int = Field(default=1000)
    chunk_overlap: int = Field(default=150)
    structured_rows_per_chunk: int = Field(default=10)
    similarity_threshold: float = Field(default=0.35)
    max_session_messages: int = Field(default=12)

    chat_rate_limit_requests: int = Field(default=30)
    chat_rate_limit_window_seconds: int = Field(default=60)
    retrieval_cache_ttl_seconds: int = Field(default=120)
    embedding_cache_ttl_seconds: int = Field(default=3600)
    session_ttl_seconds: int = Field(default=1800)
    session_storage_enabled: bool = Field(default=False)
    auth_enabled: bool = Field(default=True)
    auth_jwt_secret: str = Field(default="change-me-immediately")
    auth_jwt_algorithm: str = Field(default="HS256")
    auth_access_token_ttl_seconds: int = Field(default=3600)
    auth_bootstrap_admin_username: str = Field(default="admin")
    auth_bootstrap_admin_password: str = Field(default="change-me-immediately")
    auth_require_https: bool = Field(default=False)

    def phase_one_assumptions(self) -> dict[str, str | bool]:
        return {
            "default_generation_provider": self.default_llm_provider,
            "default_generation_model": self.default_llm_model,
            "default_embedding_provider": self.default_embedding_provider,
            "default_embedding_model": self.default_embedding_model,
            "embedding_dimension_strategy": (
                "Option A: one canonical embedding provider/model per deployed "
                "index. Request-level embedding overrides are only valid when "
                "they match the canonical configured index pair and dimension."
            ),
            "canonical_embedding_dimension": self.canonical_embedding_dimension,
            "similarity_threshold": self.similarity_threshold,
            "redis_session_storage_enabled_by_default": self.session_storage_enabled,
            "authentication_enabled_by_default": self.auth_enabled,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
