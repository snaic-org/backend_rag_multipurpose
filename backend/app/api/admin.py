from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.security import require_admin_user
from app.models.schemas import (
    AuthenticatedUser,
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
from app.services.document_inspection_service import DocumentInspectionService

router = APIRouter()


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
