import json
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.core.security import require_authenticated_user
from app.core.defaults import CHAT_MAX_RESPONSE_CHARS
from app.models.schemas import AuthenticatedUser, ChatRequest, ChatResponse
from app.services.chat_service import ChatService

router = APIRouter()


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


def _raise_chat_http_error(exc: Exception) -> None:
    message = str(exc)
    status_code = status.HTTP_400_BAD_REQUEST

    if "rate limit" in message.lower():
        status_code = status.HTTP_429_TOO_MANY_REQUESTS
    elif "quota" in message.lower():
        status_code = status.HTTP_429_TOO_MANY_REQUESTS
    elif "required" in message.lower() or "unsupported" in message.lower():
        status_code = status.HTTP_400_BAD_REQUEST
    elif "unreachable" in message.lower():
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    raise HTTPException(status_code=status_code, detail=message) from exc


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _thinking_enabled_for(settings) -> bool:
    return bool(getattr(settings, "chat_thinking_enabled", False))


@router.post("", response_model=ChatResponse)
async def chat(
    request: Request,
    payload: ChatRequest,
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
) -> ChatResponse:
    service = _build_chat_service(request)
    rate_limit_key = _resolve_rate_limit_key(current_user)

    try:
        result = await service.prepare_chat(payload, rate_limit_key)
    except Exception as exc:
        _raise_chat_http_error(exc)

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
        retrieved_chunks=result.retrieved_chunks if payload.debug else [],
    )


@router.post("/stream")
async def chat_stream(
    request: Request,
    payload: ChatRequest,
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
) -> StreamingResponse:
    service = _build_chat_service(request)
    rate_limit_key = _resolve_rate_limit_key(current_user)

    try:
        stream_state = await service.start_stream(payload, rate_limit_key)
    except Exception as exc:
        _raise_chat_http_error(exc)

    async def event_generator() -> AsyncIterator[str]:
        max_response_chars = CHAT_MAX_RESPONSE_CHARS
        yield _sse(
            "metadata",
            {
                "provider": stream_state.provider,
                "model": stream_state.model,
                "embedding_profile": stream_state.embedding_profile,
                "embedding_provider": stream_state.embedding_provider,
                "embedding_model": stream_state.embedding_model,
                "used_fallback": stream_state.used_fallback,
                "retrieved_chunks": [
                    chunk.model_dump(mode="json") for chunk in stream_state.retrieved_chunks
                ]
                if payload.debug
                else [],
            },
        )

        if stream_state.used_fallback:
            yield _sse("chunk", {"delta": stream_state.fallback_text})
            yield _sse(
                "done",
                {
                    "answer": stream_state.fallback_text,
                    "thinking": None,
                    "citations": [],
                    "used_fallback": True,
                    "retrieved_chunks": [],
                },
            )
            return

        answer_parts: list[str] = []
        answer_length = 0
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
        yield _sse(
            "done",
            {
                "answer": final_text,
                "thinking": stream_state.thinking
                if _thinking_enabled_for(request.app.state.settings)
                else None,
                "citations": [citation.model_dump(mode="json") for citation in stream_state.citations],
                "used_fallback": False,
                "retrieved_chunks": [
                    chunk.model_dump(mode="json") for chunk in stream_state.retrieved_chunks
                ]
                if payload.debug
                else [],
            },
        )

    return StreamingResponse(event_generator(), media_type="text/event-stream")
