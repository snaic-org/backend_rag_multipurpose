import time as clock
from datetime import datetime, time, timezone
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.errors import raise_value_error
from app.core.logging import get_logger
from app.core.security import require_admin_user
from app.models.schemas import (
    AnalyticsResponse,
    AuthenticatedUser,
    ChatActivityQueryResponse,
    ChatFeedbackResponse,
    ChunkRecord,
    IngestFilesResponse,
    ModelCatalogResponse,
    ModelSelectionResponse,
    ModelSelectionUpdateRequest,
    PlaygroundRequest,
    PlaygroundResponse,
    PlaygroundSource,
    IngestedDocumentDetails,
    IngestedDocumentSummary,
    ResetResponse,
    SystemPromptResponse,
    SystemPromptUpdateRequest,
    UserCreateRequest,
    UserResponse,
    UserUpdateRequest,
    WebsiteIngestRequest,
)
from app.services.ingest_service import IngestService
from app.services.reset_service import ResetService
from app.services.chat_activity_service import ChatActivityService
from app.services.chat_feedback_service import ChatFeedbackService
from app.services.document_inspection_service import DocumentInspectionService

router = APIRouter()
logger = get_logger(__name__)


def _parse_activity_datetime(value: str | None, *, end_of_day: bool) -> datetime | None:
    if value is None:
        return None

    raw = value.strip()
    if not raw:
        return None

    try:
        parsed = datetime.strptime(raw, "%d/%m/%Y")
        resolved_time = time.max if end_of_day else time.min
        return datetime.combine(parsed.date(), resolved_time, tzinfo=timezone.utc)
    except ValueError:
        pass

    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid date format. Use DD/MM/YYYY or ISO 8601 like 2026-03-29T23:59:59Z.",
        ) from exc

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _build_reset_service(request: Request) -> ResetService:
    return request.app.state.reset_service


def _build_auth_service(request: Request):
    return request.app.state.auth_service


def _build_prompt_service(request: Request):
    return request.app.state.prompt_service


def _build_model_selection_service(request: Request):
    return request.app.state.model_selection_service


def _build_chat_activity_service(request: Request) -> ChatActivityService:
    return request.app.state.activity_service


def _build_chat_feedback_service(request: Request) -> ChatFeedbackService:
    return request.app.state.feedback_service


def _build_document_inspection_service(request: Request) -> DocumentInspectionService:
    return request.app.state.document_inspection_service


def _build_ingest_service(request: Request) -> IngestService:
    return request.app.state.ingest_service


@router.delete("/reset", response_model=ResetResponse)
async def reset_backend_state(
    request: Request,
    _: AuthenticatedUser = Depends(require_admin_user),
    ) -> ResetResponse:
    service = _build_reset_service(request)
    return await service.reset_all()


@router.get("/model-catalog", response_model=ModelCatalogResponse)
async def get_model_catalog(
    request: Request,
    _: AuthenticatedUser = Depends(require_admin_user),
) -> ModelCatalogResponse:
    return await _build_model_selection_service(request).get_catalog()


@router.get("/model-selection", response_model=ModelSelectionResponse)
async def get_model_selection(
    request: Request,
    _: AuthenticatedUser = Depends(require_admin_user),
) -> ModelSelectionResponse:
    return await _build_model_selection_service(request).get_model_selection()


@router.put("/model-selection", response_model=ModelSelectionResponse)
async def update_model_selection(
    request: Request,
    payload: ModelSelectionUpdateRequest,
    _: AuthenticatedUser = Depends(require_admin_user),
) -> ModelSelectionResponse:
    settings = request.app.state.settings
    for profiles, name in (
        (settings.generation_profiles, payload.generation_profile),
        (settings.embedding_profiles, payload.embedding_profile),
    ):
        if name in profiles:
            await _require_provider_ready(request, profiles[name].provider)
    try:
        updated = await _build_model_selection_service(request).update_model_selection(
            payload.generation_profile,
            payload.embedding_profile,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    # /health/models would otherwise keep reporting the previous models for up to a day.
    health = getattr(request.app.state, "model_health_service", None)
    if health is not None:
        health.invalidate()
    return updated


@router.get("/documents", response_model=list[IngestedDocumentSummary])
async def list_documents(
    request: Request,
    limit: int = 20,
    source_type: str | None = Query(default=None, description="Filter by source type, e.g. 'website', 'txt', 'docx'"),
    _: AuthenticatedUser = Depends(require_admin_user),
) -> list[IngestedDocumentSummary]:
    return await _build_document_inspection_service(request).list_documents(limit=limit, source_type=source_type)


@router.get("/documents/{document_id}", response_model=IngestedDocumentDetails)
async def get_document(
    request: Request,
    document_id: UUID,
    _: AuthenticatedUser = Depends(require_admin_user),
) -> IngestedDocumentDetails:
    try:
        return await _build_document_inspection_service(request).get_document(document_id)
    except ValueError as exc:
        raise_value_error(exc)


@router.get("/documents/{document_id}/raw", response_model=list[ChunkRecord])
async def get_document_raw_chunks(
    request: Request,
    document_id: UUID,
    _: AuthenticatedUser = Depends(require_admin_user),
) -> list[ChunkRecord]:
    try:
        return await _build_document_inspection_service(request).get_document_chunks(document_id)
    except ValueError as exc:
        raise_value_error(exc)


@router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a document and all its chunks",
    description="Removes the document record and every associated vector chunk. Works for any source type (file, text, or website).",
)
async def delete_document(
    request: Request,
    document_id: UUID,
    _: AuthenticatedUser = Depends(require_admin_user),
) -> None:
    deleted = await _build_ingest_service(request).delete_document(document_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")


@router.post(
    "/documents/{document_id}/reingest",
    response_model=IngestFilesResponse,
    summary="Re-scrape and replace a website document",
    description="Re-fetches the stored URL, re-extracts content, and replaces the existing chunks. Only valid for documents with source_type 'website'.",
)
async def reingest_document(
    request: Request,
    document_id: UUID,
    current_user: AuthenticatedUser = Depends(require_admin_user),
) -> IngestFilesResponse:
    inspection_service = _build_document_inspection_service(request)
    try:
        details = await inspection_service.get_document(document_id)
    except ValueError as exc:
        raise_value_error(exc)

    if details.document.source_type != "website":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Reingest is only supported for website documents, got source_type='{details.document.source_type}'",
        )

    url = details.document.url
    if not url:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Document has no URL stored; cannot reingest",
        )

    payload = WebsiteIngestRequest(urls=[url], force_reingest=True)
    return await _build_ingest_service(request).ingest_websites(payload, current_user=current_user)


@router.get("/chat-activity", response_model=ChatActivityQueryResponse)
async def list_chat_activity(
    request: Request,
    limit: int = 100,
    start_at: str | None = Query(
        default=None,
        openapi_examples={
            "date_only": {
                "summary": "Date only",
                "value": "24/03/2025",
            }
        },
        description="Filter activities created at or after this date. Supports DD/MM/YYYY or ISO 8601 UTC timestamps.",
    ),
    end_at: str | None = Query(
        default=None,
        openapi_examples={
            "date_only": {
                "summary": "Date only",
                "value": "29/03/2025",
            }
        },
        description="Filter activities created at or before this date. Supports DD/MM/YYYY or ISO 8601 UTC timestamps.",
    ),
    keyword: str | None = None,
    _: AuthenticatedUser = Depends(require_admin_user),
) -> ChatActivityQueryResponse:
    bounded_limit = max(1, min(limit, 500))
    parsed_start_at = _parse_activity_datetime(start_at, end_of_day=False)
    parsed_end_at = _parse_activity_datetime(end_at, end_of_day=True)
    return await _build_chat_activity_service(request).search(
        limit=bounded_limit,
        start_at=parsed_start_at,
        end_at=parsed_end_at,
        keyword=keyword,
    )


@router.get("/analytics", response_model=AnalyticsResponse)
async def get_analytics(
    request: Request,
    days: int = Query(default=30, ge=1, le=365, description="Length of the period, ending now. Totals for the period before it are included for comparison."),
    _: AuthenticatedUser = Depends(require_admin_user),
) -> AnalyticsResponse:
    """Usage analytics for the admin dashboard: daily chats, tokens, keywords, feedback, errors and latency."""
    return await request.app.state.analytics_service.summarize(days)


@router.get("/chat-feedback", response_model=list[ChatFeedbackResponse])
async def list_chat_feedback(
    request: Request,
    limit: int = 100,
    start_at: str | None = Query(
        default=None,
        openapi_examples={
            "date_only": {
                "summary": "Date only",
                "value": "24/03/2025",
            }
        },
        description="Filter feedback created at or after this date. Supports DD/MM/YYYY or ISO 8601 UTC timestamps.",
    ),
    end_at: str | None = Query(
        default=None,
        openapi_examples={
            "date_only": {
                "summary": "Date only",
                "value": "29/03/2025",
            }
        },
        description="Filter feedback created at or before this date. Supports DD/MM/YYYY or ISO 8601 UTC timestamps.",
    ),
    _: AuthenticatedUser = Depends(require_admin_user),
) -> list[ChatFeedbackResponse]:
    bounded_limit = max(1, min(limit, 500))
    parsed_start_at = _parse_activity_datetime(start_at, end_of_day=False)
    parsed_end_at = _parse_activity_datetime(end_at, end_of_day=True)
    return await _build_chat_feedback_service(request).list_feedback(
        limit=bounded_limit,
        start_at=parsed_start_at,
        end_at=parsed_end_at,
    )


@router.post("/users", response_model=UserResponse)
async def create_user(
    request: Request,
    payload: UserCreateRequest,
    _: AuthenticatedUser = Depends(require_admin_user),
) -> UserResponse:
    try:
        return await _build_auth_service(request).create_user(payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


async def _require_provider_ready(request: Request, provider: str) -> None:
    """Refuse a model whose provider has no API key or cannot be reached, so a
    switch cannot leave the live chatbot unable to answer."""
    health = await request.app.state.providers.get(provider).healthcheck()
    if not health.ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"The {provider} provider is not ready ({health.detail}). Configure it before switching to its models.",
        )


@router.post("/playground", response_model=PlaygroundResponse)
async def playground(
    request: Request,
    payload: PlaygroundRequest,
    current_user: AuthenticatedUser = Depends(require_admin_user),
) -> PlaygroundResponse:
    """Try a question against the knowledge base with any configured model and,
    optionally, a draft system prompt. Not logged as a visitor chat; changes
    nothing."""
    settings = request.app.state.settings
    profile = payload.generation_profile or (await _build_model_selection_service(request).get_model_selection()).generation_profile
    spec = settings.generation_profiles.get(profile)
    if spec is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown model profile '{profile}'.")
    await _require_provider_ready(request, spec.provider)

    started = clock.monotonic()
    try:
        result = await request.app.state.chat_service.preview_answer(
            message=payload.message,
            provider=spec.provider,
            model=spec.model,
            rate_limit_key=f"playground:{current_user.id}",
            system_prompt=payload.system_prompt,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("playground_failed profile=%s error=%r", profile, exc)
        reason = f"HTTP {exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError) else type(exc).__name__
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"{spec.model} did not answer ({reason}).",
        ) from exc

    sources: list[PlaygroundSource] = []
    for citation in result.citations:
        if all(source.title != citation.title for source in sources):
            sources.append(PlaygroundSource(title=citation.title, url=citation.url))
    return PlaygroundResponse(
        answer=result.answer,
        generation_profile=profile,
        provider=spec.provider,
        model=spec.model,
        latency_ms=int((clock.monotonic() - started) * 1000),
        used_fallback=result.used_fallback,
        sources=sources,
    )


@router.post("/system-prompt/reset", response_model=SystemPromptResponse)
async def reset_system_prompt(
    request: Request,
    _: AuthenticatedUser = Depends(require_admin_user),
) -> SystemPromptResponse:
    """Go back to the built-in prompt; future built-in updates then apply again."""
    return await _build_prompt_service(request).reset_system_prompt()


@router.get("/system-prompt", response_model=SystemPromptResponse)
async def get_system_prompt(
    request: Request,
    _: AuthenticatedUser = Depends(require_admin_user),
) -> SystemPromptResponse:
    return await _build_prompt_service(request).get_system_prompt()


@router.put("/system-prompt", response_model=SystemPromptResponse)
async def update_system_prompt(
    request: Request,
    payload: SystemPromptUpdateRequest,
    _: AuthenticatedUser = Depends(require_admin_user),
) -> SystemPromptResponse:
    try:
        return await _build_prompt_service(request).update_system_prompt(payload.system_prompt)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/users", response_model=list[UserResponse])
async def list_users(
    request: Request,
    _: AuthenticatedUser = Depends(require_admin_user),
) -> list[UserResponse]:
    return await _build_auth_service(request).list_users()


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(
    request: Request,
    user_id: UUID,
    _: AuthenticatedUser = Depends(require_admin_user),
) -> UserResponse:
    try:
        return await _build_auth_service(request).get_user(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.patch("/users/{user_id}", response_model=UserResponse)
async def update_user(
    request: Request,
    user_id: UUID,
    payload: UserUpdateRequest,
    current_user: AuthenticatedUser = Depends(require_admin_user),
) -> UserResponse:
    try:
        return await _build_auth_service(request).update_user(user_id, payload, current_user)
    except ValueError as exc:
        raise_value_error(exc)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    request: Request,
    user_id: UUID,
    current_user: AuthenticatedUser = Depends(require_admin_user),
) -> None:
    try:
        await _build_auth_service(request).delete_user(user_id, current_user)
    except ValueError as exc:
        raise_value_error(exc)
