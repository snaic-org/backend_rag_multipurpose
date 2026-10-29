import json
from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import quote

from pydantic import BaseModel, Field
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.core.model_profiles import find_profile

ProviderName = Literal["openai", "gemini", "ollama", "nim"]

_PLACEHOLDER_SECRET = "change-me-immediately"

CHAT_REPEATED_PROMPT_LOOKBACK = 5
CHAT_MAX_CONTEXT_CHUNK_CHARS = 1800
CHAT_MAX_EXCERPTS_PER_DOCUMENT = 3
CHAT_MIN_TOP_K = 3
CHAT_MAX_TOP_K = 8
CHAT_MAX_RESPONSE_CHARS = 4000
# With reasoning enabled the hidden <think> trace shares this budget with the
# final answer, so keep enough headroom that the answer is never truncated.
CHAT_MAX_RESPONSE_TOKENS = 2560
CHAT_TOP_P = 1.0
CHAT_FREQUENCY_PENALTY = 0.0
CHAT_PRESENCE_PENALTY = 0.0
CHAT_DEBUG_ENABLED = False
EMBEDDING_CACHE_TTL_SECONDS = 3600
SESSION_TTL_SECONDS = 86400

SESSION_STORAGE_ENABLED = True
OPENAI_REASONING_EFFORT = "low"
NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
RERANK_BASE_URL = "https://ai.api.nvidia.com/v1/retrieval"

DEFAULT_GENERATION_CATALOG = [
    {
        "profile_name": "openai_gpt41_mini",
        "provider": "openai",
        "model": "gpt-4.1-mini",
    },
    {
        "profile_name": "nim_35super120",
        "provider": "nim",
        "model": "nvidia/nemotron-3.5-super-vl-120b-a12b",
    },
    {
        "profile_name": "nim_35lightning30",
        "provider": "nim",
        "model": "nvidia/nemotron-3.5-lightning-30b-a3b",
    },
    {
        "profile_name": "nim_3ultra550",
        "provider": "nim",
        "model": "nvidia/nemotron-3-ultra-550b-a55b",
    },
    {
        "profile_name": "ollama_llama32",
        "provider": "ollama",
        "model": "llama3.2",
    },
]

DEFAULT_EMBEDDING_CATALOG = [
    {
        "profile_name": "openai_small_1536",
        "provider": "openai",
        "model": "text-embedding-3-small",
        "dimension": 1536,
    },
    {
        "profile_name": "ollama_1536",
        "provider": "ollama",
        "model": "rjmalagon/gte-qwen2-1.5b-instruct-embed-f16",
        "dimension": 1536,
    },
    {
        "profile_name": "ollama_4096",
        "provider": "ollama",
        "model": "qwen3-embedding",
        "dimension": 4096,
    },
    {
        "profile_name": "nim_nemotron_2048",
        "provider": "nim",
        "model": "nvidia/nemotron-3-embed-1b",
        "dimension": 2048,
    },
]


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
    app_version: str = Field(default="0.1.0")
    app_description: str = Field(
        default=(
            "Backend RAG API built by Isfaque AL Kaderi Tuhin, Research Engineer at SNAIC. "
            "GitHub: https://github.com/iahin | "
            "Contact: shioktech@gmail.com"
        )
    )
    log_level: str = Field(default="INFO")
    # NoDecode: let the validator below split comma-separated values instead of
    # pydantic-settings requiring a JSON list.
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    auth_enabled: bool = Field(default=True)
    auth_jwt_secret: str = Field(default=_PLACEHOLDER_SECRET)
    auth_jwt_algorithm: str = Field(default="HS256")
    auth_access_token_ttl_seconds: int = Field(default=3600)
    auth_bootstrap_admin_username: str = Field(default="admin")
    auth_bootstrap_admin_password: str = Field(default=_PLACEHOLDER_SECRET)
    auth_require_https: bool = Field(default=False)

    postgres_dsn: str | None = Field(default=None)
    postgres_host: str = Field(default="localhost")
    postgres_port: int = Field(default=5432)
    postgres_db: str = Field(default="ragdb")
    postgres_user: str = Field(default="postgres")
    postgres_password: str = Field(default="postgres")
    postgres_min_pool_size: int = Field(default=1)
    postgres_max_pool_size: int = Field(default=10)

    redis_url: str = Field(default="redis://localhost:6379/0")
    redis_max_connections: int = Field(default=10)

    qdrant_url: str = Field(default="http://localhost:6333")
    qdrant_api_key: str | None = Field(default=None)
    qdrant_collection_prefix: str = Field(default="rag_chunks")

    contentful_space_id: str | None = Field(default=None)
    contentful_delivery_token: str | None = Field(default=None)
    contentful_environment: str = Field(default="master")
    contentful_site_base_url: str = Field(default="https://www.snaic.net")
    # Website ingestion refuses private/internal addresses (SSRF protection);
    # enable only to ingest intranet sites deliberately.
    website_ingest_allow_private_hosts: bool = Field(default=False)

    openai_api_key: str | None = Field(default=None)
    nim_api_key: str | None = Field(default=None)
    nim_base_url: str = Field(default=NIM_BASE_URL)
    gemini_api_key: str | None = Field(default=None)
    ollama_base_url: str = Field(default="http://localhost:11434")
    ollama_health_timeout_seconds: float = Field(default=3.0)

    rerank_enabled: bool = Field(default=False)
    rerank_invoke_url: str = Field(default="")
    rerank_model: str = Field(default="nvidia/llama-nemotron-rerank-vl-1b-v2")
    rerank_max_candidates: int = Field(default=12)
    rerank_min_candidates: int = Field(default=2)
    retrieval_multi_query_enabled: bool = Field(default=True)
    retrieval_multi_query_max_queries: int = Field(default=3)
    retrieval_source_diversity_enabled: bool = Field(default=True)
    retrieval_source_diversity_min_sources: int = Field(default=2)

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
    similarity_threshold: float = Field(default=0.35)
    max_session_messages: int = Field(default=12)

    chat_rate_limit_requests: int = Field(default=20)
    chat_rate_limit_window_seconds: int = Field(default=60)
    chat_daily_limit_requests: int = Field(default=1000)
    chat_top_k: int = Field(default=5, ge=1, le=25)
    chat_max_message_chars: int = Field(default=4000)
    chat_max_input_tokens: int = Field(default=4000)
    chat_max_history_messages: int = Field(default=8)
    chat_max_context_chars: int = Field(default=8000)
    chat_max_context_tokens: int = Field(default=2500)
    chat_max_excerpts_per_document: int = Field(default=CHAT_MAX_EXCERPTS_PER_DOCUMENT)
    chat_debug_enabled: bool = Field(default=CHAT_DEBUG_ENABLED)
    chat_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    chat_thinking_enabled: bool = Field(default=False)
    chat_show_thinking_block: bool = Field(default=False)
    retrieval_cache_ttl_seconds: int = Field(default=120)
    health_model_probe_ttl_seconds: int = Field(default=300, ge=0)
    health_model_probe_timeout_seconds: float = Field(default=20.0, gt=0)

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def normalize_cors_allowed_origins(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                value = json.loads(stripped)
            else:
                value = stripped.split(",")
        if isinstance(value, list):
            return [str(origin).strip().rstrip("/") for origin in value if str(origin).strip()]
        return value

    @field_validator(
        "default_generation_provider",
        "default_generation_model",
        "default_embedding_provider",
        "default_embedding_model",
        mode="before",
    )
    @classmethod
    def strip_default_model_settings(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @model_validator(mode="after")
    def resolve_postgres_dsn(self) -> "Settings":
        dsn = (self.postgres_dsn or "").strip()
        if dsn and "<CHANGE_ME_DB_PASSWORD>" not in dsn:
            self.postgres_dsn = dsn
            return self

        encoded_user = quote(self.postgres_user, safe="")
        encoded_password = quote(self.postgres_password, safe="")
        encoded_db = quote(self.postgres_db, safe="")
        self.postgres_dsn = (
            f"postgresql://{encoded_user}:{encoded_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{encoded_db}"
        )
        return self

    def insecure_setting_warnings(self) -> list[str]:
        """Settings that are unsafe outside local development."""
        warnings: list[str] = []
        if not self.auth_enabled:
            warnings.append("AUTH_ENABLED=false: every caller is treated as an admin")
        if self.auth_enabled and (
            self.auth_jwt_secret == _PLACEHOLDER_SECRET or len(self.auth_jwt_secret) < 32
        ):
            warnings.append("AUTH_JWT_SECRET is the placeholder or shorter than 32 characters")
        if self.auth_enabled and self.auth_bootstrap_admin_password == _PLACEHOLDER_SECRET:
            warnings.append("AUTH_BOOTSTRAP_ADMIN_PASSWORD is still the placeholder value")
        if self.chat_debug_enabled:
            warnings.append("CHAT_DEBUG_ENABLED=true: chat responses expose prompts, retrieved chunks and history")
        return warnings

    @property
    def default_generation_profile(self) -> str:
        if self.default_generation_provider is None or self.default_generation_model is None:
            raise ValueError("DEFAULT_GENERATION_PROVIDER and DEFAULT_GENERATION_MODEL are required")
        return find_profile(
            self.generation_profiles,
            "default generation provider/model pair",
            provider=self.default_generation_provider,
            model=self.default_generation_model,
        )

    @property
    def default_embedding_profile(self) -> str:
        if (
            self.default_embedding_provider is None
            or self.default_embedding_model is None
            or self.default_embedding_dimension is None
        ):
            raise ValueError(
                "DEFAULT_EMBEDDING_PROVIDER, DEFAULT_EMBEDDING_MODEL and DEFAULT_EMBEDDING_DIMENSION are required"
            )
        return find_profile(
            self.embedding_profiles,
            "default embedding provider/model/dimension triple",
            provider=self.default_embedding_provider,
            model=self.default_embedding_model,
            dimension=self.default_embedding_dimension,
        )


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
