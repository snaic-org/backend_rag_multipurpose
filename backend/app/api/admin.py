from datetime import datetime, time, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.core.security import require_admin_user
from app.models.schemas import (
    AuthenticatedUser,
    ChatActivityQueryResponse,
    ChatFeedbackResponse,
    ChunkRecord,
    ModelCatalogResponse,
    ModelSelectionResponse,
    ModelSelectionUpdateRequest,
    IngestedDocumentDetails,
    IngestedDocumentSummary,
    ResetResponse,
    SystemPromptResponse,
    SystemPromptUpdateRequest,
    UserCreateRequest,
    UserResponse,
    UserUpdateRequest,
)
from app.services.reset_service import ResetService
from app.services.chat_activity_service import ChatActivityService
from app.services.chat_feedback_service import ChatFeedbackService
from app.services.document_inspection_service import DocumentInspectionService

router = APIRouter()


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
    return ResetService(
        postgres_pool=request.app.state.postgres.pool,
        qdrant_manager=request.app.state.qdrant,
        redis_manager=request.app.state.redis,
    )


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
    return DocumentInspectionService(
        settings=request.app.state.settings,
        postgres_pool=request.app.state.postgres.pool,
        qdrant_manager=request.app.state.qdrant,
    )


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
    try:
        return await _build_model_selection_service(request).update_model_selection(
            payload.generation_profile,
            payload.embedding_profile,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/documents", response_model=list[IngestedDocumentSummary])
async def list_documents(
    request: Request,
    limit: int = 20,
    _: AuthenticatedUser = Depends(require_admin_user),
) -> list[IngestedDocumentSummary]:
    return await _build_document_inspection_service(request).list_documents(limit=limit)


@router.get("/documents/{document_id}", response_model=IngestedDocumentDetails)
async def get_document(
    request: Request,
    document_id: UUID,
    _: AuthenticatedUser = Depends(require_admin_user),
) -> IngestedDocumentDetails:
    try:
        return await _build_document_inspection_service(request).get_document(document_id)
    except ValueError as exc:
        message = str(exc)
        status_code = status.HTTP_400_BAD_REQUEST
        if "not found" in message.lower():
            status_code = status.HTTP_404_NOT_FOUND
        raise HTTPException(status_code=status_code, detail=message) from exc


@router.get("/documents/{document_id}/raw", response_model=list[ChunkRecord])
async def get_document_raw_chunks(
    request: Request,
    document_id: UUID,
    _: AuthenticatedUser = Depends(require_admin_user),
) -> list[ChunkRecord]:
    try:
        return await _build_document_inspection_service(request).get_document_chunks(document_id)
    except ValueError as exc:
        message = str(exc)
        status_code = status.HTTP_400_BAD_REQUEST
        if "not found" in message.lower():
            status_code = status.HTTP_404_NOT_FOUND
        raise HTTPException(status_code=status_code, detail=message) from exc


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
        message = str(exc)
        status_code = status.HTTP_400_BAD_REQUEST
        if "not found" in message.lower():
            status_code = status.HTTP_404_NOT_FOUND
        raise HTTPException(status_code=status_code, detail=message) from exc


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    request: Request,
    user_id: UUID,
    current_user: AuthenticatedUser = Depends(require_admin_user),
) -> None:
    try:
        await _build_auth_service(request).delete_user(user_id, current_user)
    except ValueError as exc:
        message = str(exc)
        status_code = status.HTTP_400_BAD_REQUEST
        if "not found" in message.lower():
            status_code = status.HTTP_404_NOT_FOUND
        raise HTTPException(status_code=status_code, detail=message) from exc
