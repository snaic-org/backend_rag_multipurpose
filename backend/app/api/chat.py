import json
from typing import AsyncIterator
from hashlib import sha256

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse

from app.core.logging import get_logger
from app.core.security import require_authenticated_user
from app.core.config import CHAT_MAX_RESPONSE_CHARS
from app.models.schemas import (
    AuthenticatedUser,
    ChatActivityWrite,
    ChatCitation,
    ChatFeedbackRequest,
    ChatFeedbackResponse,
    ChatPublicResponse,
    ChatRequest,
    ChatResponse,
    ChatServiceResult,
    ChatStreamState,
)
from app.services.chat_activity_service import ChatActivityService
from app.services.chat_feedback_service import ChatFeedbackService
from app.services.chat_service import ChatService

router = APIRouter()
logger = get_logger(__name__)
CHAT_SESSION_COOKIE = "rag_chat_session"
CHAT_SESSION_HEADER = "X-Chat-Session-Id"

CHAT_RESPONSE_DESCRIPTION = (
    "Default response contains only answer, compact citations, and session_id. "
    "The server manages session history with a browser session cookie. "
    "When CHAT_DEBUG_ENABLED=true, the response includes the full debug shape "
    "including message_history."
)

CHAT_STREAM_OPENAPI_RESPONSE = {
    "description": (
        "Server-sent events. With CHAT_DEBUG_ENABLED=false, chunk events contain "
        "`answer` and the final done event contains `answer`, compact `citations`, "
        "and `session_id`. The server manages session history with a browser "
        "session cookie. With CHAT_DEBUG_ENABLED=true, metadata and done events "
        "include the full debug payload including `message_history`."
    ),
    "content": {
        "text/event-stream": {
            "examples": {
                "default": {
                    "summary": "Default stream",
                    "value": (
                        "event: chunk\n"
                        "data: {\"answer\":\"We offer AI chatbot implementation.\"}\n\n"
                        "event: done\n"
                        "data: {\"answer\":\"We offer AI chatbot implementation.\","
                        "\"citations\":[{\"document_id\":\"22222222-2222-2222-2222-222222222222\","
                        "\"chunk_id\":\"33333333-3333-3333-3333-333333333333\"}],"
                        "\"session_id\":\"11111111-1111-1111-1111-111111111111\"}\n\n"
                    ),
                },
                "debug": {
                    "summary": "Debug stream",
                    "value": (
                        "event: metadata\n"
                        "data: {\"provider\":\"ollama\",\"model\":\"llama3.2\","
                        "\"embedding_profile\":\"ollama_1536\",\"used_fallback\":false,"
                        "\"retrieved_chunks\":[],\"prompt_messages\":[],\"message_history\":[],"
                        "\"session_id\":\"11111111-1111-1111-1111-111111111111\"}\n\n"
                        "event: chunk\n"
                        "data: {\"delta\":\"We offer AI chatbot implementation.\"}\n\n"
                    ),
                },
            }
        }
    },
}


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


def _resolve_chat_session_id(request: Request) -> str:
    cookie_value = (request.cookies.get(CHAT_SESSION_COOKIE) or "").strip()
    if cookie_value and len(cookie_value) <= 128:
        return cookie_value
    header_value = (request.headers.get(CHAT_SESSION_HEADER) or "").strip()
    if header_value and len(header_value) <= 128:
        return header_value
    return _server_side_public_session_id(request)


def _server_side_public_session_id(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    client_host = request.client.host if request.client is not None else ""
    fingerprint = "|".join(
        [
            forwarded_for or client_host,
            request.headers.get("user-agent", ""),
            request.headers.get("origin", ""),
            request.headers.get("referer", ""),
        ]
    ).strip("|")
    if not fingerprint:
        return "public-chat-session"
    digest = sha256(fingerprint.encode("utf-8")).hexdigest()[:32]
    return f"public-{digest}"


def _request_is_https(request: Request) -> bool:
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
    return forwarded_proto == "https" or request.url.scheme == "https"


def _set_chat_session_cookie(response: Response, request: Request, session_id: str) -> None:
    secure_cookie = _request_is_https(request)
    response.headers[CHAT_SESSION_HEADER] = session_id
    response.set_cookie(
        key=CHAT_SESSION_COOKIE,
        value=session_id,
        httponly=True,
        secure=secure_cookie,
        samesite="none" if secure_cookie else "lax",
    )


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


def _public_citations(citations: list[ChatCitation]) -> list[dict[str, str]]:
    return [
        {
            "document_id": str(citation.document_id),
            "chunk_id": str(citation.chunk_id),
        }
        for citation in citations
    ]


def _public_stream_done_payload(
    *,
    answer: str,
    citations: list[ChatCitation],
    session_id: str | None,
) -> dict:
    return {
        "answer": answer,
        "citations": _public_citations(citations),
        "session_id": session_id,
    }


def _public_chat_payload(result: ChatServiceResult) -> dict:
    return {
        "answer": result.answer,
        "citations": _public_citations(result.citations),
        "session_id": result.session_id,
    }


def _debug_chat_payload(result: ChatServiceResult) -> dict:
    return {
        "answer": result.answer,
        "thinking": result.thinking,
        "citations": [citation.model_dump(mode="json") for citation in result.citations],
        "provider": result.provider,
        "model": result.model,
        "embedding_profile": result.embedding_profile,
        "embedding_provider": result.embedding_provider,
        "embedding_model": result.embedding_model,
        "used_fallback": result.used_fallback,
        "session_id": result.session_id,
        "retrieved_chunks": [
            chunk.model_dump(mode="json", exclude_none=True)
            for chunk in result.retrieved_chunks
        ],
        "prompt_messages": [
            message.model_dump(mode="json", exclude_none=True)
            for message in result.prompt_messages
        ],
        "message_history": [
            message.model_dump(mode="json", exclude_none=True)
            for message in result.message_history
        ],
    }


def _debug_stream_metadata_payload(stream_state: ChatStreamState) -> dict:
    return {
        "provider": stream_state.provider,
        "model": stream_state.model,
        "embedding_profile": stream_state.embedding_profile,
        "embedding_provider": stream_state.embedding_provider,
        "embedding_model": stream_state.embedding_model,
        "used_fallback": stream_state.used_fallback,
        "retrieved_chunks": [
            chunk.model_dump(mode="json")
            for chunk in stream_state.retrieved_chunks
        ],
        "prompt_messages": [
            message.model_dump(mode="json", exclude_none=True)
            for message in stream_state.prompt_messages
        ],
        "message_history": [
            message.model_dump(mode="json", exclude_none=True)
            for message in stream_state.message_history
        ],
        "session_id": stream_state.session_id,
    }


def _debug_stream_done_payload(
    *,
    request: Request,
    stream_state: ChatStreamState,
    answer: str,
    citations: list[ChatCitation],
) -> dict:
    return {
        "answer": answer,
        "thinking": stream_state.thinking
        if _thinking_enabled_for(request.app.state.settings)
        else None,
        "citations": [citation.model_dump(mode="json") for citation in citations],
        "used_fallback": stream_state.used_fallback,
        "retrieved_chunks": [
            chunk.model_dump(mode="json")
            for chunk in stream_state.retrieved_chunks
        ],
        "prompt_messages": [
            message.model_dump(mode="json", exclude_none=True)
            for message in stream_state.prompt_messages
        ],
        "message_history": [
            message.model_dump(mode="json", exclude_none=True)
            for message in stream_state.message_history
        ],
        "provider": stream_state.provider,
        "model": stream_state.model,
        "embedding_profile": stream_state.embedding_profile,
        "embedding_provider": stream_state.embedding_provider,
        "embedding_model": stream_state.embedding_model,
        "session_id": stream_state.session_id,
    }


def _thinking_enabled_for(settings) -> bool:
    return bool(getattr(settings, "chat_thinking_enabled", False))


def _debug_enabled_for(settings) -> bool:
    return bool(getattr(settings, "chat_debug_enabled", False))


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
    session_id: str | None = None,
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
        session_id=session_id,
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
        metadata={
            "debug": _debug_enabled_for(request.app.state.settings),
            "top_k": getattr(request.app.state.settings, "chat_top_k", 5),
        },
    )


async def _record_activity_safe(
    activity_service: ChatActivityService,
    activity_payload: ChatActivityWrite,
) -> None:
    try:
        await activity_service.record(activity_payload)
    except Exception as exc:  # pragma: no cover - defensive logging path
        logger.warning("chat_activity_record_failed: %s", exc)


@router.post(
    "",
    response_model=ChatResponse | ChatPublicResponse,
    response_description=CHAT_RESPONSE_DESCRIPTION,
)
async def chat(
    request: Request,
    response: Response,
    payload: ChatRequest,
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
) -> dict:
    service = _build_chat_service(request)
    activity_service = _build_chat_activity_service(request)
    rate_limit_key = _resolve_rate_limit_key(current_user)
    session_id = _resolve_chat_session_id(request)
    _set_chat_session_cookie(response, request, session_id)
    debug_enabled = _debug_enabled_for(request.app.state.settings)

    try:
        result = await service.prepare_chat(payload, rate_limit_key, session_id=session_id)
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
            session_id=result.session_id,
        )
    )

    if debug_enabled:
        return _debug_chat_payload(result)
    return _public_chat_payload(result)


@router.post(
    "/stream",
    responses={status.HTTP_200_OK: CHAT_STREAM_OPENAPI_RESPONSE},
)
async def chat_stream(
    request: Request,
    payload: ChatRequest,
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
) -> StreamingResponse:
    service = _build_chat_service(request)
    activity_service = _build_chat_activity_service(request)
    rate_limit_key = _resolve_rate_limit_key(current_user)
    session_id = _resolve_chat_session_id(request)
    debug_enabled = _debug_enabled_for(request.app.state.settings)

    try:
        stream_state = await service.start_stream(payload, rate_limit_key, session_id=session_id)
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
        if debug_enabled:
            yield _sse("metadata", _debug_stream_metadata_payload(stream_state))

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
                    session_id=stream_state.session_id,
                )
            )
            chunk_payload = (
                {"delta": stream_state.fallback_text}
                if debug_enabled
                else {"answer": stream_state.fallback_text}
            )
            yield _sse("chunk", chunk_payload)
            done_payload = (
                _debug_stream_done_payload(
                    request=request,
                    stream_state=stream_state,
                    answer=stream_state.fallback_text,
                    citations=[],
                )
                if debug_enabled
                else _public_stream_done_payload(
                    answer=stream_state.fallback_text,
                    citations=[],
                    session_id=stream_state.session_id,
                )
            )
            yield _sse("done", done_payload)
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
                    chunk_payload = {"delta": chunk} if debug_enabled else {"answer": chunk}
                    yield _sse("chunk", chunk_payload)
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
                    session_id=stream_state.session_id,
                )
            )
            done_payload = (
                _debug_stream_done_payload(
                    request=request,
                    stream_state=stream_state,
                    answer=final_text,
                    citations=stream_state.citations,
                )
                if debug_enabled
                else _public_stream_done_payload(
                    answer=final_text,
                    citations=stream_state.citations,
                    session_id=stream_state.session_id,
                )
            )
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
                    session_id=stream_state.session_id,
                )
            )
            raise

    response = StreamingResponse(event_generator(), media_type="text/event-stream")
    _set_chat_session_cookie(response, request, session_id)
    return response


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
