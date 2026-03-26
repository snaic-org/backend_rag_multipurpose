from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.defaults import (
    CHAT_MAX_RESPONSE_CHARS,
    CHAT_MAX_RESPONSE_TOKENS,
    CHAT_FREQUENCY_PENALTY,
    CHAT_PRESENCE_PENALTY,
    DEFAULT_EMBEDDING_CATALOG,
    DEFAULT_GENERATION_CATALOG,
    NIM_BASE_URL,
    CHAT_TOP_P,
    SESSION_STORAGE_ENABLED,
)

ProviderName = Literal["openai", "gemini", "ollama", "nim"]


class EmbeddingProfileSpec(BaseModel):
    provider: ProviderName
    model: str
    dimension: int


class GenerationProfileSpec(BaseModel):
    provider: ProviderName
    model: str


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

    openai_api_key: str | None = Field(default=None)
    nim_api_key: str | None = Field(default=None)
    nim_base_url: str = Field(default=NIM_BASE_URL)

    gemini_api_key: str | None = Field(default=None)

    ollama_base_url: str = Field(default="http://localhost:11434")
    ollama_health_timeout_seconds: float = Field(default=3.0)

    rerank_enabled: bool = Field(default=False)
    rerank_invoke_url: str = Field(default="")
    rerank_model: str = Field(default="nvidia/llama-nemotron-rerank-1b-v2")
    rerank_max_candidates: int = Field(default=12)
    rerank_min_candidates: int = Field(default=2)

    default_generation_provider: ProviderName | None = Field(default=None)
    default_generation_model: str | None = Field(default=None)
    default_embedding_provider: ProviderName | None = Field(default=None)
    default_embedding_model: str | None = Field(default=None)
    default_embedding_dimension: int | None = Field(default=None)

    generation_profiles: dict[str, GenerationProfileSpec] = Field(
        default_factory=lambda: _default_generation_profiles()
    )
    embedding_profiles: dict[str, EmbeddingProfileSpec] = Field(
        default_factory=lambda: _default_embedding_profiles()
    )
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
    chat_max_context_chars: int = Field(default=8000)
    chat_max_context_tokens: int = Field(default=2500)
    chat_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    chat_thinking_enabled: bool = Field(default=False)
    chat_show_thinking_block: bool = Field(default=False)
    retrieval_cache_ttl_seconds: int = Field(default=120)
    auth_enabled: bool = Field(default=True)
    auth_jwt_secret: str = Field(default="change-me-immediately")
    auth_jwt_algorithm: str = Field(default="HS256")
    auth_access_token_ttl_seconds: int = Field(default=3600)
    auth_bootstrap_admin_username: str = Field(default="admin")
    auth_bootstrap_admin_password: str = Field(default="change-me-immediately")
    auth_require_https: bool = Field(default=False)

    @field_validator("default_generation_provider", mode="before")
    @classmethod
    def normalize_default_generation_provider(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("default_generation_model", mode="before")
    @classmethod
    def normalize_default_generation_model(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("default_embedding_provider", mode="before")
    @classmethod
    def normalize_default_embedding_provider(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("default_embedding_model", mode="before")
    @classmethod
    def normalize_default_embedding_model(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    def phase_one_assumptions(self) -> dict[str, object]:
        return {
            "model_selection_source": "database",
            "default_generation_provider": self.default_generation_provider,
            "default_generation_model": self.default_generation_model,
            "default_generation_profile": self.default_generation_profile,
            "default_embedding_provider": self.default_embedding_provider,
            "default_embedding_model": self.default_embedding_model,
            "default_embedding_dimension": self.default_embedding_dimension,
            "default_embedding_profile": self.default_embedding_profile,
            "nim_base_url": self.nim_base_url,
            "rerank_enabled": self.rerank_enabled,
            "rerank_invoke_url": self.rerank_invoke_url,
            "rerank_model": self.rerank_model,
            "rerank_max_candidates": self.rerank_max_candidates,
            "rerank_min_candidates": self.rerank_min_candidates,
            "embedding_dimension_strategy": (
                "Named embedding profiles resolve to one canonical provider/model "
                "pair each. Request-level overrides are only valid when they map "
                "to a configured profile whose dimension matches the deployed index."
            ),
            "configured_generation_profiles": {
                name: profile.model_dump() for name, profile in self.generation_profiles.items()
            },
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
            "chat_max_response_chars": CHAT_MAX_RESPONSE_CHARS,
            "chat_max_response_tokens": CHAT_MAX_RESPONSE_TOKENS,
            "chat_temperature": self.chat_temperature,
            "chat_top_p": CHAT_TOP_P,
            "chat_frequency_penalty": CHAT_FREQUENCY_PENALTY,
            "chat_presence_penalty": CHAT_PRESENCE_PENALTY,
            "chat_thinking_enabled": self.chat_thinking_enabled,
            "chat_show_thinking_block": self.chat_show_thinking_block,
            "redis_session_storage_enabled_by_default": SESSION_STORAGE_ENABLED,
            "authentication_enabled_by_default": self.auth_enabled,
        }

    @property
    def default_generation_profile(self) -> str:
        provider = self._require_default_generation_provider()
        model = self._require_default_generation_model()
        return _resolve_generation_profile_name(
            self.generation_profiles,
            provider,
            model,
        )

    @property
    def default_embedding_profile(self) -> str:
        provider = self._require_default_embedding_provider()
        model = self._require_default_embedding_model()
        dimension = self._require_default_embedding_dimension()
        return _resolve_embedding_profile_name(
            self.embedding_profiles,
            provider,
            model,
            dimension,
        )

    def _require_default_generation_provider(self) -> ProviderName:
        if self.default_generation_provider is None:
            raise ValueError("DEFAULT_GENERATION_PROVIDER is required")
        return self.default_generation_provider

    def _require_default_generation_model(self) -> str:
        if self.default_generation_model is None:
            raise ValueError("DEFAULT_GENERATION_MODEL is required")
        return self.default_generation_model

    def _require_default_embedding_provider(self) -> ProviderName:
        if self.default_embedding_provider is None:
            raise ValueError("DEFAULT_EMBEDDING_PROVIDER is required")
        return self.default_embedding_provider

    def _require_default_embedding_model(self) -> str:
        if self.default_embedding_model is None:
            raise ValueError("DEFAULT_EMBEDDING_MODEL is required")
        return self.default_embedding_model

    def _require_default_embedding_dimension(self) -> int:
        if self.default_embedding_dimension is None:
            raise ValueError("DEFAULT_EMBEDDING_DIMENSION is required")
        return self.default_embedding_dimension


@lru_cache
def get_settings() -> Settings:
    return Settings()


def _default_generation_profiles() -> dict[str, GenerationProfileSpec]:
    return {
        item["profile_name"]: GenerationProfileSpec(
            provider=item["provider"],
            model=item["model"],
        )
        for item in DEFAULT_GENERATION_CATALOG
    }


def _default_embedding_profiles() -> dict[str, EmbeddingProfileSpec]:
    return {
        item["profile_name"]: EmbeddingProfileSpec(
            provider=item["provider"],
            model=item["model"],
            dimension=item["dimension"],
        )
        for item in DEFAULT_EMBEDDING_CATALOG
    }


def _resolve_generation_profile_name(
    catalog: dict[str, GenerationProfileSpec],
    provider: ProviderName,
    model: str,
) -> str:
    matches = [
        name
        for name, spec in catalog.items()
        if spec.provider == provider and spec.model == model
    ]
    if not matches:
        raise ValueError(f"Unknown default generation provider/model pair '{provider}/{model}'")
    if len(matches) > 1:
        raise ValueError(
            f"Multiple generation profiles match the default provider/model pair '{provider}/{model}'"
        )
    return matches[0]


def _resolve_embedding_profile_name(
    catalog: dict[str, EmbeddingProfileSpec],
    provider: ProviderName,
    model: str,
    dimension: int,
) -> str:
    matches = [
        name
        for name, spec in catalog.items()
        if spec.provider == provider and spec.model == model and spec.dimension == dimension
    ]
    if not matches:
        raise ValueError(
            f"Unknown default embedding provider/model/dimension triple '{provider}/{model}/{dimension}'"
        )
    if len(matches) > 1:
        raise ValueError(
            "Multiple embedding profiles match the default provider/model/dimension triple"
        )
    return matches[0]
