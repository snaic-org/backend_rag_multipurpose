import json
from typing import AsyncIterator

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.core.logging import get_logger
from app.core.security import require_authenticated_user
from app.core.defaults import CHAT_MAX_RESPONSE_CHARS
from app.models.schemas import (
    AuthenticatedUser,
    ChatActivityWrite,
    ChatFeedbackRequest,
    ChatFeedbackResponse,
    ChatRequest,
    ChatResponse,
)
from app.services.chat_activity_service import ChatActivityService
from app.services.chat_feedback_service import ChatFeedbackService
from app.services.chat_service import ChatService

router = APIRouter()
logger = get_logger(__name__)


class _NullChatActivityService:
    async def record(self, payload: ChatActivityWrite) -> None:
        return None


class _NullChatFeedbackService:
    async def submit_feedback(
        self,
        payload: ChatFeedbackRequest,
        current_user: AuthenticatedUser,
    ) -> ChatFeedbackResponse:
        raise RuntimeError("chat feedback service is unavailable")


def _build_chat_service(request: Request) -> ChatService:
    return ChatService(
        settings=request.app.state.settings,
        qdrant_manager=request.app.state.qdrant,
        redis_manager=request.app.state.redis,
        provider_registry=request.app.state.providers,
        system_prompt_service=request.app.state.prompt_service,
        model_selection_service=request.app.state.model_selection_service,
    )


def _resolve_rate_limit_key(current_user: AuthenticatedUser) -> str:
    return f"user:{current_user.id}"


def _build_chat_activity_service(request: Request) -> ChatActivityService:
    return getattr(request.app.state, "activity_service", _NullChatActivityService())


def _build_chat_feedback_service(request: Request) -> ChatFeedbackService:
    return getattr(request.app.state, "feedback_service", _NullChatFeedbackService())


def _raise_chat_http_error(exc: Exception) -> None:
    message = str(exc)
    status_code = status.HTTP_400_BAD_REQUEST

    if isinstance(exc, httpx.HTTPStatusError):
        upstream_status = exc.response.status_code
        status_code = (
            status.HTTP_503_SERVICE_UNAVAILABLE
            if upstream_status >= 500
            else status.HTTP_502_BAD_GATEWAY
        )
    elif isinstance(exc, httpx.HTTPError):
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    elif "rate limit" in message.lower():
        status_code = status.HTTP_429_TOO_MANY_REQUESTS
    elif "quota" in message.lower():
        status_code = status.HTTP_429_TOO_MANY_REQUESTS
    elif "required" in message.lower() or "unsupported" in message.lower():
        status_code = status.HTTP_400_BAD_REQUEST
    elif "unreachable" in message.lower() or "failed with status 5" in message.lower():
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    raise HTTPException(status_code=status_code, detail=message) from exc


def _raise_feedback_http_error(exc: Exception) -> None:
    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _thinking_enabled_for(settings) -> bool:
    return bool(getattr(settings, "chat_thinking_enabled", False))


def _extract_forwarded_for(request: Request) -> list[str]:
    header = request.headers.get("x-forwarded-for", "")
    return [part.strip() for part in header.split(",") if part.strip()]


def _resolve_client_ip(request: Request, forwarded_for: list[str]) -> str | None:
    if forwarded_for:
        return forwarded_for[0]
    if request.client is not None:
        return request.client.host
    return None


def _build_activity_payload(
    request: Request,
    current_user: AuthenticatedUser,
    payload: ChatRequest,
    *,
    status_value: str,
    response_answer: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    embedding_profile: str | None = None,
    embedding_provider: str | None = None,
    embedding_model: str | None = None,
    used_fallback: bool = False,
    citations_count: int = 0,
    retrieved_chunks_count: int = 0,
    error_message: str | None = None,
) -> ChatActivityWrite:
    forwarded_for = _extract_forwarded_for(request)
    return ChatActivityWrite(
        user_id=current_user.id,
        username=current_user.username,
        auth_type=current_user.auth_type,
        request_path=request.url.path,
        client_ip=_resolve_client_ip(request, forwarded_for),
        forwarded_for=forwarded_for,
        user_agent=request.headers.get("user-agent"),
        session_id=payload.session_id,
        request_message=payload.message,
        response_answer=response_answer,
        provider=provider,
        model=model,
        embedding_profile=embedding_profile,
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        used_fallback=used_fallback,
        citations_count=citations_count,
        retrieved_chunks_count=retrieved_chunks_count,
        status=status_value,
        error_message=error_message,
        metadata={"debug": payload.debug, "top_k": payload.top_k},
    )


async def _record_activity_safe(
    activity_service: ChatActivityService,
    activity_payload: ChatActivityWrite,
) -> None:
    try:
        await activity_service.record(activity_payload)
    except Exception as exc:  # pragma: no cover - defensive logging path
        logger.warning("chat_activity_record_failed: %s", exc)


@router.post("", response_model=ChatResponse, response_model_exclude_none=True)
async def chat(
    request: Request,
    payload: ChatRequest,
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
) -> ChatResponse:
    service = _build_chat_service(request)
    activity_service = _build_chat_activity_service(request)
    rate_limit_key = _resolve_rate_limit_key(current_user)

    try:
        result = await service.prepare_chat(payload, rate_limit_key)
    except Exception as exc:
        await _record_activity_safe(
            activity_service,
            _build_activity_payload(
                request,
                current_user,
                payload,
                status_value="failed",
                error_message=str(exc),
            )
        )
        _raise_chat_http_error(exc)

    await _record_activity_safe(
        activity_service,
        _build_activity_payload(
            request,
            current_user,
            payload,
            status_value="completed",
            response_answer=result.answer,
            provider=result.provider,
            model=result.model,
            embedding_profile=result.embedding_profile,
            embedding_provider=result.embedding_provider,
            embedding_model=result.embedding_model,
            used_fallback=result.used_fallback,
            citations_count=len(result.citations),
            retrieved_chunks_count=len(result.retrieved_chunks),
        )
    )

    return ChatResponse(
        answer=result.answer,
        thinking=result.thinking,
        citations=result.citations,
        provider=result.provider,
        model=result.model,
        embedding_profile=result.embedding_profile,
        embedding_provider=result.embedding_provider,
        embedding_model=result.embedding_model,
        used_fallback=result.used_fallback,
        session_id=result.session_id,
        retrieved_chunks=result.retrieved_chunks if payload.debug else [],
    )


@router.post("/stream")
async def chat_stream(
    request: Request,
    payload: ChatRequest,
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
) -> StreamingResponse:
    service = _build_chat_service(request)
    activity_service = _build_chat_activity_service(request)
    rate_limit_key = _resolve_rate_limit_key(current_user)

    try:
        stream_state = await service.start_stream(payload, rate_limit_key)
    except Exception as exc:
        await _record_activity_safe(
            activity_service,
            _build_activity_payload(
                request,
                current_user,
                payload,
                status_value="failed",
                error_message=str(exc),
            )
        )
        _raise_chat_http_error(exc)

    async def event_generator() -> AsyncIterator[str]:
        max_response_chars = CHAT_MAX_RESPONSE_CHARS
        metadata_payload = {
            "provider": stream_state.provider,
            "model": stream_state.model,
            "embedding_profile": stream_state.embedding_profile,
            "embedding_provider": stream_state.embedding_provider,
            "embedding_model": stream_state.embedding_model,
            "used_fallback": stream_state.used_fallback,
            "retrieved_chunks": [
                chunk.model_dump(mode="json", exclude_none=True) for chunk in stream_state.retrieved_chunks
            ]
            if payload.debug
            else [],
        }
        if stream_state.session_id is not None:
            metadata_payload["session_id"] = stream_state.session_id
        yield _sse(
            "metadata",
            metadata_payload,
        )

        if stream_state.used_fallback:
            await _record_activity_safe(
                activity_service,
                _build_activity_payload(
                    request,
                    current_user,
                    payload,
                    status_value="completed",
                    response_answer=stream_state.fallback_text,
                    provider=stream_state.provider,
                    model=stream_state.model,
                    embedding_profile=stream_state.embedding_profile,
                    embedding_provider=stream_state.embedding_provider,
                    embedding_model=stream_state.embedding_model,
                    used_fallback=True,
                    citations_count=0,
                    retrieved_chunks_count=0,
                )
            )
            yield _sse("chunk", {"delta": stream_state.fallback_text})
            done_payload = {
                "answer": stream_state.fallback_text,
                "thinking": None,
                "citations": [],
                "used_fallback": True,
                "retrieved_chunks": [],
            }
            if stream_state.session_id is not None:
                done_payload["session_id"] = stream_state.session_id
            yield _sse(
                "done",
                done_payload,
            )
            return

        answer_parts: list[str] = []
        answer_length = 0
        try:
            async for delta in stream_state.stream:
                if not delta:
                    continue
                remaining = max_response_chars - answer_length
                if remaining <= 0:
                    break
                chunk = delta[:remaining]
                if chunk:
                    answer_parts.append(chunk)
                    answer_length += len(chunk)
                    yield _sse("chunk", {"delta": chunk})
                if len(delta) > len(chunk):
                    break

            final_text = service.finalize_answer("".join(answer_parts))
            await service.finalize_stream(stream_state, final_text)
            await _record_activity_safe(
                activity_service,
                _build_activity_payload(
                    request,
                    current_user,
                    payload,
                    status_value="completed",
                    response_answer=final_text,
                    provider=stream_state.provider,
                    model=stream_state.model,
                    embedding_profile=stream_state.embedding_profile,
                    embedding_provider=stream_state.embedding_provider,
                    embedding_model=stream_state.embedding_model,
                    used_fallback=False,
                    citations_count=len(stream_state.citations),
                    retrieved_chunks_count=len(stream_state.retrieved_chunks),
                )
            )
            done_payload = {
                "answer": final_text,
                "thinking": stream_state.thinking
                if _thinking_enabled_for(request.app.state.settings)
                else None,
                "citations": [citation.model_dump(mode="json") for citation in stream_state.citations],
                "used_fallback": False,
                "retrieved_chunks": [
                    chunk.model_dump(mode="json", exclude_none=True)
                    for chunk in stream_state.retrieved_chunks
                ]
                if payload.debug
                else [],
            }
            if stream_state.session_id is not None:
                done_payload["session_id"] = stream_state.session_id
            yield _sse("done", done_payload)
        except Exception as exc:
            partial_answer = service.finalize_answer("".join(answer_parts))
            await _record_activity_safe(
                activity_service,
                _build_activity_payload(
                    request,
                    current_user,
                    payload,
                    status_value="failed",
                    response_answer=partial_answer or None,
                    provider=stream_state.provider,
                    model=stream_state.model,
                    embedding_profile=stream_state.embedding_profile,
                    embedding_provider=stream_state.embedding_provider,
                    embedding_model=stream_state.embedding_model,
                    error_message=str(exc),
                    citations_count=len(stream_state.citations),
                    retrieved_chunks_count=len(stream_state.retrieved_chunks),
                )
            )
            raise

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/feedback", response_model=ChatFeedbackResponse)
async def submit_chat_feedback(
    request: Request,
    payload: ChatFeedbackRequest,
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
) -> ChatFeedbackResponse:
    service = _build_chat_feedback_service(request)
    try:
        return await service.submit_feedback(payload, current_user)
    except Exception as exc:
        _raise_feedback_http_error(exc)
