from functools import lru_cache
import json
from typing import Literal

from pydantic import BaseModel, Field
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderName = Literal["openai", "gemini", "ollama", "nim"]


class EmbeddingProfileSpec(BaseModel):
    provider: ProviderName
    model: str
    dimension: int


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

    qdrant_url: str = Field(default="http://localhost:6333")
    qdrant_api_key: str | None = Field(default=None)
    qdrant_collection_prefix: str = Field(default="rag_chunks")

    openai_enabled: bool = Field(default=False)
    openai_api_key: str | None = Field(default=None)
    nim_enabled: bool = Field(default=False)
    nim_api_key: str | None = Field(default=None)
    nim_base_url: str = Field(default="")
    nim_no_think: bool = Field(default=True)

    gemini_enabled: bool = Field(default=False)
    gemini_api_key: str | None = Field(default=None)

    ollama_enabled: bool = Field(default=True)
    ollama_base_url: str = Field(default="http://localhost:11434")
    ollama_health_timeout_seconds: float = Field(default=3.0)

    rerank_enabled: bool = Field(default=False)
    rerank_invoke_url: str = Field(default="")
    rerank_model: str = Field(default="nvidia/llama-nemotron-rerank-1b-v2")
    rerank_max_candidates: int = Field(default=12)
    rerank_min_candidates: int = Field(default=2)

    default_llm_provider: ProviderName = Field(default="ollama")
    default_llm_model: str = Field(default="llama3.2")
    default_embedding_profile: str = Field(default="")
    embedding_profiles: dict[str, EmbeddingProfileSpec] = Field(default_factory=dict)
    chunk_size: int = Field(default=1000)
    chunk_overlap: int = Field(default=150)
    structured_rows_per_chunk: int = Field(default=10)
    similarity_threshold: float = Field(default=0.35)
    max_session_messages: int = Field(default=12)

    chat_rate_limit_requests: int = Field(default=20)
    chat_rate_limit_window_seconds: int = Field(default=60)
    chat_daily_limit_requests: int = Field(default=1000)
    chat_max_message_chars: int = Field(default=4000)
    chat_max_input_tokens: int = Field(default=1000)
    chat_max_history_messages: int = Field(default=8)
    chat_repeated_prompt_lookback: int = Field(default=5)
    chat_max_context_chars: int = Field(default=8000)
    chat_max_context_tokens: int = Field(default=2500)
    chat_max_context_chunk_chars: int = Field(default=1800)
    chat_min_top_k: int = Field(default=3)
    chat_max_top_k: int = Field(default=8)
    chat_max_response_chars: int = Field(default=2000)
    chat_max_response_tokens: int = Field(default=700)
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

    @field_validator("default_embedding_profile", mode="before")
    @classmethod
    def normalize_default_embedding_profile(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("embedding_profiles", mode="before")
    @classmethod
    def parse_embedding_profiles(cls, value: object) -> object:
        if value in (None, ""):
            return {}
        if isinstance(value, str):
            parsed = json.loads(value)
            if not isinstance(parsed, dict):
                raise ValueError("EMBEDDING_PROFILES must decode to an object")
            return parsed
        return value

    @property
    def default_embedding_spec(self) -> EmbeddingProfileSpec:
        if not self.default_embedding_profile:
            raise ValueError("DEFAULT_EMBEDDING_PROFILE is required")
        profile = self.embedding_profiles.get(self.default_embedding_profile)
        if profile is None:
            raise ValueError(
                f"Unknown default embedding profile '{self.default_embedding_profile}'"
            )
        return profile

    @property
    def default_embedding_provider(self) -> ProviderName:
        return self.default_embedding_spec.provider

    @property
    def default_embedding_model(self) -> str:
        return self.default_embedding_spec.model

    @property
    def canonical_embedding_dimension(self) -> int:
        return self.default_embedding_spec.dimension

    def phase_one_assumptions(self) -> dict[str, object]:
        return {
            "default_generation_provider": self.default_llm_provider,
            "default_generation_model": self.default_llm_model,
            "nim_enabled": self.nim_enabled,
            "nim_base_url": self.nim_base_url,
            "nim_no_think": self.nim_no_think,
            "rerank_enabled": self.rerank_enabled,
            "rerank_invoke_url": self.rerank_invoke_url,
            "rerank_model": self.rerank_model,
            "rerank_max_candidates": self.rerank_max_candidates,
            "rerank_min_candidates": self.rerank_min_candidates,
            "default_embedding_profile": self.default_embedding_profile,
            "default_embedding_provider": self.default_embedding_provider,
            "default_embedding_model": self.default_embedding_model,
            "embedding_dimension_strategy": (
                "Named embedding profiles resolve to one canonical provider/model "
                "pair each. Request-level overrides are only valid when they map "
                "to a configured profile whose dimension matches the deployed index."
            ),
            "canonical_embedding_dimension": self.canonical_embedding_dimension,
            "configured_embedding_profiles": {
                name: profile.model_dump() for name, profile in self.embedding_profiles.items()
            },
            "similarity_threshold": self.similarity_threshold,
            "chat_rate_limit_requests": self.chat_rate_limit_requests,
            "chat_daily_limit_requests": self.chat_daily_limit_requests,
            "chat_max_message_chars": self.chat_max_message_chars,
            "chat_max_input_tokens": self.chat_max_input_tokens,
            "chat_max_context_chars": self.chat_max_context_chars,
            "chat_max_context_tokens": self.chat_max_context_tokens,
            "chat_max_response_chars": self.chat_max_response_chars,
            "chat_max_response_tokens": self.chat_max_response_tokens,
            "redis_session_storage_enabled_by_default": self.session_storage_enabled,
            "authentication_enabled_by_default": self.auth_enabled,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
