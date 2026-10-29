import re
from dataclasses import dataclass

from app.core.config import (
    CHAT_MAX_CONTEXT_CHUNK_CHARS,
    EMBEDDING_CACHE_TTL_SECONDS,
    SESSION_STORAGE_ENABLED,
    SESSION_TTL_SECONDS,
    Settings,
)
from app.db.qdrant import QdrantManager
from app.db.redis import RedisManager
from app.models.schemas import (
    ChatMessage,
    ChatRequest,
    ChatServiceResult,
    ChatStreamState,
    GenerationSelection,
    ModelSelectionResponse,
)
from app.providers.common import contains_thinking_block, strip_thinking_blocks
from app.providers.registry import ProviderRegistry
from app.services.guardrails import GuardrailService
from app.services.embeddings import EmbeddingService
from app.services.assistant_copy import SAFE_FALLBACK_TEXT
from app.services.cache_service import CacheService
from app.services.model_selection_service import ModelSelectionService
from app.services.prompt_builder import PromptBuilder
from app.services.query_planner import QueryPlannerService
from app.services.system_prompt_service import SystemPromptService
from app.services.retrieval import RetrievalService
from app.services.session_service import SessionService

# Words that make a short message depend on the previous exchange.
_REFERENCE_WORDS = frozenset(
    {
        "it", "its", "that", "this", "these", "those", "them", "they", "their",
        "he", "she", "him", "his", "her", "more", "else", "another", "other",
        "one", "ones", "same", "previous", "above", "first", "second", "third",
        "fourth", "fifth", "last", "next", "each", "both", "all",
    }
)
_FOLLOW_UP_CONTEXT_CHARS = 1500
# The prompt asks for hyphens, but models still emit em and en dashes.
_DASHES = str.maketrans({"\u2014": "-", "\u2013": "-"})
# Messages up to this many words are checked for references to the previous
# exchange, and also searched together with the previous question.
_SHORT_MESSAGE_WORDS = 8


class ChatService:
    def __init__(
        self,
        settings: Settings,
        qdrant_manager: QdrantManager,
        redis_manager: RedisManager,
        provider_registry: ProviderRegistry,
        system_prompt_service: SystemPromptService,
        model_selection_service: ModelSelectionService,
    ) -> None:
        self._settings = settings
        self._providers = provider_registry
        self._embedding_service = EmbeddingService(
            settings,
            cache_service=CacheService(redis_manager.client, ttl_seconds=EMBEDDING_CACHE_TTL_SECONDS),
        )
        self._retrieval_service = RetrievalService(settings, qdrant_manager, redis_manager)
        self._prompt_builder = PromptBuilder()
        self._query_planner = QueryPlannerService(settings)
        self._system_prompt_service = system_prompt_service
        self._model_selection_service = model_selection_service
        self._guardrails = GuardrailService(settings, redis_manager.client)
        self._session_service = SessionService(
            redis_client=redis_manager.client,
            ttl_seconds=SESSION_TTL_SECONDS,
            enabled=SESSION_STORAGE_ENABLED,
            max_messages=settings.max_session_messages,
        )

    async def prepare_chat(
        self,
        payload: ChatRequest,
        rate_limit_key: str,
        session_id: str | None = None,
    ) -> ChatServiceResult:
        prepared = await self._prepare_chat_context(payload, rate_limit_key, session_id)

        if prepared.used_fallback:
            message_history = await self._append_turn_to_history(
                prepared.session_id,
                prepared.message_history,
                prepared.user_message,
                prepared.fallback_text,
            )
            return ChatServiceResult(
                answer=prepared.fallback_text,
                thinking=None,
                citations=[],
                provider=prepared.provider,
                model=prepared.model,
                embedding_profile=prepared.embedding_profile,
                embedding_provider=prepared.embedding_provider,
                embedding_model=prepared.embedding_model,
                used_fallback=True,
                session_id=prepared.session_id,
                retrieved_chunks=[],
                prompt_messages=[],
                message_history=message_history,
            )

        completion = await self._providers.get(prepared.provider).complete_chat(
            messages=prepared.prompt_messages,
            model=prepared.model,
        )
        completion_text = completion.text or ""
        completion_thinking = completion.thinking or None
        answer = self.finalize_answer(self._format_answer(completion_text, completion_thinking))

        message_history = await self._append_turn_to_history(
            prepared.session_id,
            prepared.message_history,
            prepared.user_message,
            answer,
        )

        return ChatServiceResult(
            answer=answer,
            thinking=completion_thinking if self._settings.chat_thinking_enabled else None,
            citations=prepared.citations,
            provider=prepared.provider,
            model=prepared.model,
            embedding_profile=prepared.embedding_profile,
            embedding_provider=prepared.embedding_provider,
            embedding_model=prepared.embedding_model,
            used_fallback=False,
            session_id=prepared.session_id,
            retrieved_chunks=prepared.retrieved_chunks,
            prompt_messages=prepared.prompt_messages,
            message_history=message_history,
        )

    async def start_stream(
        self,
        payload: ChatRequest,
        rate_limit_key: str,
        session_id: str | None = None,
    ) -> ChatStreamState:
        prepared = await self._prepare_chat_context(payload, rate_limit_key, session_id)

        if prepared.used_fallback:
            message_history = await self._append_turn_to_history(
                prepared.session_id,
                prepared.message_history,
                prepared.user_message,
                prepared.fallback_text,
            )
            return ChatStreamState(
                provider=prepared.provider,
                model=prepared.model,
                embedding_profile=prepared.embedding_profile,
                embedding_provider=prepared.embedding_provider,
                embedding_model=prepared.embedding_model,
                citations=[],
                retrieved_chunks=[],
                thinking=None,
                stream=None,
                used_fallback=True,
                fallback_text=prepared.fallback_text,
                session_id=prepared.session_id,
                user_message=prepared.user_message,
                prompt_messages=[],
                message_history=message_history,
            )

        stream = self._providers.get(prepared.provider).stream_chat(
            messages=prepared.prompt_messages,
            model=prepared.model,
        )

        return ChatStreamState(
            provider=prepared.provider,
            model=prepared.model,
            embedding_profile=prepared.embedding_profile,
            embedding_provider=prepared.embedding_provider,
            embedding_model=prepared.embedding_model,
            citations=prepared.citations,
            retrieved_chunks=prepared.retrieved_chunks,
            thinking=None,
            stream=stream,
            used_fallback=False,
            fallback_text="",
            session_id=prepared.session_id,
            user_message=prepared.user_message,
            prompt_messages=prepared.prompt_messages,
            message_history=prepared.message_history,
        )

    async def finalize_stream(
        self,
        stream_state: ChatStreamState,
        answer: str,
    ) -> None:
        if stream_state.used_fallback:
            return

        stream_state.message_history = await self._append_turn_to_history(
            stream_state.session_id,
            stream_state.message_history,
            stream_state.user_message,
            answer,
        )

    @staticmethod
    def plain_dashes(text: str) -> str:
        """Replace em and en dashes with hyphens; the length is unchanged, so it
        is safe on streamed chunks."""
        return text.translate(_DASHES)

    def finalize_answer(self, text: str) -> str:
        cleaned = self.plain_dashes(self._strip_user_question_preamble(text))
        return self._guardrails.truncate_response(cleaned)

    def _strip_user_question_preamble(self, text: str | None) -> str:
        if not text:
            return ""
        cleaned = text.strip()
        patterns = (
            r"^The user's question[^.\n]*\.\s*",
            r"^The user is asking[^.\n]*\.\s*",
            r"^Based on the conversation history[^.\n]*\.\s*",
            r"^This refers to[^.\n]*\.\s*",
        )
        previous = None
        while previous != cleaned:
            previous = cleaned
            for pattern in patterns:
                cleaned = re.sub(pattern, "", cleaned, count=1, flags=re.IGNORECASE)
        return cleaned.strip()

    async def _prepare_chat_context(
        self,
        payload: ChatRequest,
        rate_limit_key: str,
        session_id: str | None,
    ) -> "_PreparedChatContext":
        await self._guardrails.enforce_request_budget(rate_limit_key)

        model_selection = await self._model_selection_service.get_model_selection()
        generation = self._resolve_generation_selection(model_selection)
        default_embedding_profile = model_selection.embedding_profile
        session_id = self._resolve_session_id(session_id)
        session_messages = await self._session_service.get_messages(session_id)
        history = session_messages
        history = self._guardrails.limit_history(history)
        recent_user_messages = [message.content for message in history if message.role == "user"]
        normalized_message = self._guardrails.validate_user_message(payload.message, recent_user_messages)
        top_k = self._guardrails.clamp_top_k(self._settings.chat_top_k)
        retrieval_message = self._build_retrieval_message(normalized_message, history)
        planned_queries = self._query_planner.build_queries(retrieval_message) or [retrieval_message]
        topic_query = self._build_topic_query(normalized_message, retrieval_message, history)
        if topic_query and topic_query.lower() not in {query.lower() for query in planned_queries}:
            planned_queries = [*planned_queries, topic_query]

        embedding_selection, query_embeddings = await self._embedding_service.embed_texts(
            texts=planned_queries,
            profile_name=default_embedding_profile,
            provider=None,
            model=None,
            input_type="query",
        )
        query_embedding = query_embeddings[0]

        retrieved_chunks = await self._retrieval_service.retrieve(
            query_text=retrieval_message,
            query_embedding=query_embedding,
            query_variants=planned_queries,
            query_variant_embeddings=query_embeddings,
            selection=embedding_selection,
            top_k=top_k,
        )

        if not retrieved_chunks:
            return _PreparedChatContext(
                provider=generation.provider,
                model=generation.model,
                embedding_profile=embedding_selection.profile_name,
                embedding_provider=embedding_selection.provider,
                embedding_model=embedding_selection.model,
                prompt_messages=[],
                thinking=None,
                citations=[],
                used_fallback=True,
                fallback_text=SAFE_FALLBACK_TEXT,
                session_id=session_id,
                user_message=normalized_message,
                retrieved_chunks=[],
                message_history=history,
            )

        prompt_config = await self._system_prompt_service.get_system_prompt()
        prompt_context = self._prompt_builder.build(
            user_message=normalized_message,
            chat_history=history,
            retrieved_chunks=retrieved_chunks,
            max_history_messages=self._settings.chat_max_history_messages,
            max_context_chars=self._settings.chat_max_context_chars,
            max_context_tokens=self._settings.chat_max_context_tokens,
            max_chunk_chars=CHAT_MAX_CONTEXT_CHUNK_CHARS,
            system_prompt=prompt_config.system_prompt,
        )
        return _PreparedChatContext(
            provider=generation.provider,
            model=generation.model,
            embedding_profile=embedding_selection.profile_name,
            embedding_provider=embedding_selection.provider,
            embedding_model=embedding_selection.model,
            prompt_messages=prompt_context.messages,
            retrieved_chunks=retrieved_chunks,
            thinking=None,
            citations=prompt_context.citations,
            used_fallback=False,
            fallback_text="",
            session_id=session_id,
            user_message=normalized_message,
            message_history=history,
        )

    async def _append_turn_to_history(
        self,
        session_id: str | None,
        existing_history: list[ChatMessage],
        user_message: str,
        assistant_answer: str,
    ) -> list[ChatMessage]:
        turn = [
            ChatMessage(role="user", content=user_message),
            ChatMessage(role="assistant", content=assistant_answer),
        ]
        await self._session_service.append_messages(session_id, turn)
        return (existing_history + turn)[-self._settings.max_session_messages :]

    def _resolve_session_id(self, session_id: str | None) -> str | None:
        if session_id is None:
            return None
        resolved = session_id.strip()
        return resolved or None

    def _build_retrieval_message(
        self,
        user_message: str,
        history: list[ChatMessage],
    ) -> str:
        if not history:
            return user_message
        if not self._is_contextual_follow_up(user_message):
            return user_message

        # The last exchange is what a follow-up refers to; cap it so a long earlier
        # answer does not drown out the new question in the embedding.
        recent_history = [message for message in history[-2:] if message.content.strip()]
        history_text = "\n".join(
            f"{message.role}: {message.content.strip()[:_FOLLOW_UP_CONTEXT_CHARS]}"
            for message in recent_history
        )
        if not history_text:
            return user_message
        return f"{history_text}\nuser follow-up: {user_message}"

    def _build_topic_query(
        self,
        user_message: str,
        retrieval_message: str,
        history: list[ChatMessage],
    ) -> str | None:
        """Pair a short message with the previous question as an extra search.

        A follow-up such as "Can you list them by group" names no topic of its
        own; searched alone it finds nothing. The extra query carries the topic
        forward, while the message's own query still finds new topics.
        """
        if retrieval_message != user_message:
            return None  # Already searched together with the previous exchange.
        if len(re.findall(r"[a-z0-9']+", user_message.lower())) > _SHORT_MESSAGE_WORDS:
            return None
        previous_question = next(
            (message.content.strip() for message in reversed(history) if message.role == "user" and message.content.strip()),
            "",
        )
        if not previous_question:
            return None
        return f"{previous_question[:_FOLLOW_UP_CONTEXT_CHARS]} {user_message}"

    def _is_contextual_follow_up(self, user_message: str) -> bool:
        normalized = user_message.strip().lower()
        words = re.findall(r"[a-z0-9']+", normalized)
        if len(words) <= 2:
            return True
        # A short message is only a follow-up when it refers back to something;
        # "Where is SNAIC located?" is short but stands on its own.
        if len(words) <= _SHORT_MESSAGE_WORDS and any(word in _REFERENCE_WORDS for word in words):
            return True
        follow_up_patterns = (
            r"\b(tell|explain|describe)\s+me\s+(more\s+)?about\s+(\d+|it|that|this|them)\b",
            r"\b(more|details?)\s+(about|on)\s+(\d+|it|that|this|them)\b",
            r"\b(what|how)\s+about\s+(\d+|it|that|this|them)\b",
            r"\b(the\s+)?(first|second|third|fourth|fifth|sixth|last)\s+(one|item|stage|step)\b",
        )
        return any(re.search(pattern, normalized) for pattern in follow_up_patterns)

    def _resolve_generation_selection(self, model_selection: ModelSelectionResponse) -> GenerationSelection:
        if model_selection.generation_provider not in self._providers.supported_provider_names():
            raise ValueError(f"Unsupported generation provider '{model_selection.generation_provider}'")
        return GenerationSelection(
            provider=model_selection.generation_provider,
            model=model_selection.generation_model,
        )

    def _format_answer(self, text: str | None, thinking: str | None) -> str:
        text = text or ""
        if self._settings.chat_show_thinking_block:
            if thinking and not contains_thinking_block(text):
                return f"<thinking>\n{thinking}\n</thinking>\n\n{text}".strip()
            return text

        return strip_thinking_blocks(text)


@dataclass
class _PreparedChatContext:
    provider: str
    model: str
    embedding_profile: str
    embedding_provider: str
    embedding_model: str
    prompt_messages: list[ChatMessage]
    retrieved_chunks: list
    thinking: str | None
    citations: list
    used_fallback: bool
    fallback_text: str
    session_id: str | None
    user_message: str
    message_history: list[ChatMessage]
