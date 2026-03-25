from app.core.config import Settings
from app.db.qdrant import QdrantManager
from app.db.redis import RedisManager
from app.models.schemas import (
    ChatMessage,
    ChatRequest,
    ChatServiceResult,
    ChatStreamState,
    GenerationSelection,
)
from app.providers.registry import ProviderRegistry
from app.services.guardrails import GuardrailService
from app.services.embeddings import EmbeddingService
from app.services.prompt_builder import PromptBuilder, SAFE_FALLBACK_TEXT
from app.services.retrieval import RetrievalService
from app.services.session_service import SessionService


class ChatService:
    def __init__(
        self,
        settings: Settings,
        qdrant_manager: QdrantManager,
        redis_manager: RedisManager,
        provider_registry: ProviderRegistry,
    ) -> None:
        self._settings = settings
        self._providers = provider_registry
        self._embedding_service = EmbeddingService(settings)
        self._retrieval_service = RetrievalService(settings, qdrant_manager, redis_manager)
        self._prompt_builder = PromptBuilder()
        self._guardrails = GuardrailService(settings, redis_manager.client)
        self._session_service = SessionService(
            redis_client=redis_manager.client,
            ttl_seconds=settings.session_ttl_seconds,
            enabled=settings.session_storage_enabled,
            max_messages=settings.max_session_messages,
        )

    async def prepare_chat(
        self,
        payload: ChatRequest,
        rate_limit_key: str,
    ) -> ChatServiceResult:
        prepared = await self._prepare_chat_context(payload, rate_limit_key)

        if prepared.used_fallback:
            return ChatServiceResult(
                answer=prepared.fallback_text,
                citations=[],
                provider=prepared.provider,
                model=prepared.model,
                embedding_provider=prepared.embedding_provider,
                embedding_model=prepared.embedding_model,
                used_fallback=True,
                retrieved_chunks=[],
            )

        completion = await self._providers.get(prepared.provider).complete_chat(
            messages=prepared.prompt_messages,
            model=prepared.model,
        )
        answer = self._guardrails.truncate_response(completion.text)

        await self._session_service.append_messages(
            prepared.session_id,
            [
                ChatMessage(role="user", content=prepared.user_message),
                ChatMessage(role="assistant", content=answer),
            ],
        )

        return ChatServiceResult(
            answer=answer,
            citations=prepared.citations,
            provider=prepared.provider,
            model=prepared.model,
            embedding_provider=prepared.embedding_provider,
            embedding_model=prepared.embedding_model,
            used_fallback=False,
            retrieved_chunks=prepared.retrieved_chunks,
        )

    async def start_stream(
        self,
        payload: ChatRequest,
        rate_limit_key: str,
    ) -> ChatStreamState:
        prepared = await self._prepare_chat_context(payload, rate_limit_key)

        if prepared.used_fallback:
            return ChatStreamState(
                provider=prepared.provider,
                model=prepared.model,
                embedding_provider=prepared.embedding_provider,
                embedding_model=prepared.embedding_model,
                citations=[],
                stream=None,
                used_fallback=True,
                fallback_text=prepared.fallback_text,
                session_id=prepared.session_id,
                user_message=prepared.user_message,
            )

        stream = self._providers.get(prepared.provider).stream_chat(
            messages=prepared.prompt_messages,
            model=prepared.model,
        )

        return ChatStreamState(
            provider=prepared.provider,
            model=prepared.model,
            embedding_provider=prepared.embedding_provider,
            embedding_model=prepared.embedding_model,
            citations=prepared.citations,
            stream=stream,
            used_fallback=False,
            fallback_text="",
            session_id=prepared.session_id,
            user_message=prepared.user_message,
        )

    async def finalize_stream(
        self,
        stream_state: ChatStreamState,
        answer: str,
    ) -> None:
        if stream_state.used_fallback:
            return

        await self._session_service.append_messages(
            stream_state.session_id,
            [
                ChatMessage(role="user", content=stream_state.user_message),
                ChatMessage(role="assistant", content=answer),
            ],
        )

    async def _prepare_chat_context(
        self,
        payload: ChatRequest,
        rate_limit_key: str,
    ) -> "_PreparedChatContext":
        await self._guardrails.enforce_request_budget(rate_limit_key)

        generation = self._resolve_generation_selection(payload.provider, payload.model)
        session_messages = await self._session_service.get_messages(payload.session_id)
        history = session_messages + payload.chat_history
        history = self._guardrails.limit_history(history)
        recent_user_messages = [message.content for message in history if message.role == "user"]
        normalized_message = self._guardrails.validate_user_message(payload.message, recent_user_messages)
        top_k = self._guardrails.clamp_top_k(payload.top_k)

        embedding_selection, query_embeddings = await self._embedding_service.embed_texts(
            texts=[normalized_message],
            profile_name=payload.embedding_profile,
            provider=payload.embedding_provider,
            model=payload.embedding_model,
            input_type="query",
        )
        query_embedding = query_embeddings[0]

        retrieved_chunks = await self._retrieval_service.retrieve(
            query_text=normalized_message,
            query_embedding=query_embedding,
            selection=embedding_selection,
            top_k=top_k,
        )

        if not retrieved_chunks:
            return _PreparedChatContext(
                provider=generation.provider,
                model=generation.model,
                embedding_provider=embedding_selection.provider,
                embedding_model=embedding_selection.model,
                prompt_messages=[],
                citations=[],
                used_fallback=True,
                fallback_text=SAFE_FALLBACK_TEXT,
                session_id=payload.session_id,
                user_message=normalized_message,
                retrieved_chunks=[],
            )

        prompt_context = self._prompt_builder.build(
            user_message=normalized_message,
            chat_history=history,
            retrieved_chunks=retrieved_chunks,
            max_history_messages=self._settings.chat_max_history_messages,
            max_context_chars=self._settings.chat_max_context_chars,
            max_context_tokens=self._settings.chat_max_context_tokens,
            max_chunk_chars=self._settings.chat_max_context_chunk_chars,
            disable_reasoning=(
                generation.provider == "nim" and self._settings.nim_no_think
            ),
        )
        return _PreparedChatContext(
            provider=generation.provider,
            model=generation.model,
            embedding_provider=embedding_selection.provider,
            embedding_model=embedding_selection.model,
            prompt_messages=prompt_context.messages,
            citations=prompt_context.citations,
            used_fallback=False,
            fallback_text="",
            session_id=payload.session_id,
            user_message=normalized_message,
            retrieved_chunks=retrieved_chunks,
        )

    def _resolve_generation_selection(
        self,
        provider: str | None,
        model: str | None,
    ) -> GenerationSelection:
        resolved_provider = provider or self._settings.default_llm_provider
        resolved_model = model or self._settings.default_llm_model

        if resolved_provider not in self._providers.supported_provider_names():
            raise ValueError(f"Unsupported generation provider '{resolved_provider}'")

        return GenerationSelection(provider=resolved_provider, model=resolved_model)


class _PreparedChatContext:
    def __init__(
        self,
        provider: str,
        model: str,
        embedding_provider: str,
        embedding_model: str,
        prompt_messages: list[ChatMessage],
        citations: list,
        used_fallback: bool,
        fallback_text: str,
        session_id: str | None,
        user_message: str,
        retrieved_chunks: list,
    ) -> None:
        self.provider = provider
        self.model = model
        self.embedding_provider = embedding_provider
        self.embedding_model = embedding_model
        self.prompt_messages = prompt_messages
        self.citations = citations
        self.used_fallback = used_fallback
        self.fallback_text = fallback_text
        self.session_id = session_id
        self.user_message = user_message
        self.retrieved_chunks = retrieved_chunks
