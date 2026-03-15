from datetime import datetime
from typing import Any, AsyncIterator, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

ProviderName = Literal["openai", "gemini", "ollama"]
SupportedTextSourceType = Literal["text", "markdown", "unstructured"]
SupportedFileSourceType = Literal["txt", "md", "docx", "csv", "xlsx"]


class DependencyHealth(BaseModel):
    ok: bool
    detail: str


class ProviderHealth(DependencyHealth):
    enabled: bool = True
    provider: str
    capabilities: list[str] = Field(default_factory=list)
    configuration_present: bool = True


class HealthCheckResponse(BaseModel):
    status: str
    app: str
    postgres: DependencyHealth
    redis: DependencyHealth
    providers: dict[str, ProviderHealth]
    assumptions: dict[str, Any]


class NormalizedDocument(BaseModel):
    title: str
    source_type: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    url: str | None = None
    original_filename: str | None = None
    mime_type: str | None = None
    sections: list[dict[str, Any]] = Field(default_factory=list)


class TextIngestItem(BaseModel):
    title: str
    content: str
    source_type: SupportedTextSourceType = "text"
    url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("content")
    @classmethod
    def validate_content_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be empty")
        return value


class IngestTextRequest(BaseModel):
    items: list[TextIngestItem] = Field(min_length=1)
    embedding_provider: ProviderName | None = None
    embedding_model: str | None = None


class IngestFileResult(BaseModel):
    filename: str
    detected_type: str
    success: bool
    chunks_created: int = 0
    error: str | None = None
    document_id: UUID | None = None


class IngestSummary(BaseModel):
    documents_inserted: int
    chunks_inserted: int
    embedding_provider: ProviderName
    embedding_model: str
    failures: list[str] = Field(default_factory=list)


class IngestFilesResponse(BaseModel):
    total_files: int
    succeeded: int
    failed: int
    total_chunks_inserted: int
    embedding_provider: ProviderName
    embedding_model: str
    results: list[IngestFileResult]


class IngestTextResponse(BaseModel):
    documents_inserted: int
    chunks_inserted: int
    embedding_provider: ProviderName
    embedding_model: str
    results: list[IngestFileResult]


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatCitation(BaseModel):
    document_id: UUID
    chunk_id: UUID
    title: str
    url: str | None = None
    source_type: str
    snippet: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    chat_history: list[ChatMessage] = Field(default_factory=list)
    top_k: int = Field(default=5, ge=1, le=25)
    provider: ProviderName | None = None
    model: str | None = None
    embedding_provider: ProviderName | None = None
    embedding_model: str | None = None

    @field_validator("message")
    @classmethod
    def validate_message_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be empty")
        return value


class ChatResponse(BaseModel):
    answer: str
    citations: list[ChatCitation] = Field(default_factory=list)
    provider: ProviderName
    model: str
    embedding_provider: ProviderName
    embedding_model: str
    used_fallback: bool = False


class DocumentRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    url: str | None = None
    source_type: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    original_filename: str | None = None
    mime_type: str | None = None
    embedding_provider: str
    embedding_model: str
    created_at: datetime
    updated_at: datetime


class ChunkUpsert(BaseModel):
    chunk_index: int = Field(ge=0)
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    embedding: list[float]

    @field_validator("content")
    @classmethod
    def validate_chunk_content_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be empty")
        return value

    @field_validator("embedding")
    @classmethod
    def validate_embedding_not_empty(cls, value: list[float]) -> list[float]:
        if not value:
            raise ValueError("embedding must not be empty")
        return value


class ChunkRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    chunk_index: int
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    embedding_provider: str
    embedding_model: str
    created_at: datetime


class ParsedFile(BaseModel):
    filename: str
    detected_type: str
    documents: list[NormalizedDocument] = Field(default_factory=list)


class EmbeddingSelection(BaseModel):
    provider: ProviderName
    model: str
    dimension: int


class GenerationSelection(BaseModel):
    provider: ProviderName
    model: str


class RetrievedChunk(BaseModel):
    chunk_id: UUID
    document_id: UUID
    title: str
    url: str | None = None
    source_type: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    similarity_score: float


class PromptContext(BaseModel):
    system_prompt: str
    messages: list[ChatMessage]
    citations: list[ChatCitation]


class ChatCompletionResult(BaseModel):
    text: str
    provider: ProviderName
    model: str


class ChatServiceResult(BaseModel):
    answer: str
    citations: list[ChatCitation] = Field(default_factory=list)
    provider: ProviderName
    model: str
    embedding_provider: ProviderName
    embedding_model: str
    used_fallback: bool = False
    retrieved_chunks: list[RetrievedChunk] = Field(default_factory=list)


class ChatStreamState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    provider: ProviderName
    model: str
    embedding_provider: ProviderName
    embedding_model: str
    citations: list[ChatCitation] = Field(default_factory=list)
    stream: AsyncIterator[str] | None = None
    used_fallback: bool = False
    fallback_text: str = ""
    session_id: str | None = None
    user_message: str = ""


class ResetResponse(BaseModel):
    status: str
    documents_deleted: int
    chunks_deleted: int
    redis_keys_deleted: int


class AccessTokenRequest(BaseModel):
    username: str
    password: str


class AuthenticatedUser(BaseModel):
    id: UUID
    username: str
    is_admin: bool
    auth_type: Literal["bearer", "api_key"]


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_seconds: int
    user: AuthenticatedUser


class ApiKeyCreateRequest(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def validate_name_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be empty")
        return value.strip()


class ApiKeyCreateResponse(BaseModel):
    api_key: str
    key_prefix: str
    name: str
    created_at: datetime


class UserRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    password_hash: str
    is_active: bool
    is_admin: bool
    created_at: datetime
    updated_at: datetime


class ApiKeyRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    name: str
    key_prefix: str
    key_hash: str
    is_active: bool
    last_used_at: datetime | None = None
    created_at: datetime
