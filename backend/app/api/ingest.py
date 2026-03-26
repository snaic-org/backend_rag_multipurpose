import json

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status

from app.models.schemas import (
    IngestFilesResponse,
    IngestTextRequest,
    IngestTextResponse,
)
from app.services.ingest_service import IngestService

router = APIRouter()


def _build_ingest_service(request: Request) -> IngestService:
    return IngestService(
        settings=request.app.state.settings,
        redis_manager=request.app.state.redis,
        qdrant_manager=request.app.state.qdrant,
        postgres_pool=request.app.state.postgres.pool,
        provider_registry=request.app.state.providers,
        model_selection_service=request.app.state.model_selection_service,
    )


def _normalize_optional_form_value(raw: str | None) -> str | None:
    if raw is None:
        return None

    normalized = raw.strip()
    if not normalized:
        return None

    # Swagger UI commonly submits the literal placeholder "string" for optional
    # multipart text fields unless the user clears them explicitly.
    if normalized.lower() == "string":
        return None

    return normalized


def _parse_tags(raw: str | None) -> list[str]:
    normalized = _normalize_optional_form_value(raw)
    if normalized is None:
        return []

    try:
        loaded = json.loads(normalized)
    except json.JSONDecodeError:
        return [item.strip() for item in normalized.split(",") if item.strip()]

    if isinstance(loaded, list):
        return [str(item) for item in loaded]

    if isinstance(loaded, str):
        return [loaded] if loaded.strip() else []

    raise ValueError("tags must be a JSON array, JSON string, or comma-separated string")


def _parse_metadata(raw: str | None) -> dict:
    normalized = _normalize_optional_form_value(raw)
    if normalized is None:
        return {}

    try:
        loaded = json.loads(normalized)
    except json.JSONDecodeError:
        return {"raw_metadata": normalized}

    if isinstance(loaded, dict):
        return loaded

    if isinstance(loaded, str):
        return {"raw_metadata": loaded}

    raise ValueError("metadata must be a JSON object or plain string")


@router.post("/text", response_model=IngestTextResponse)
async def ingest_text(request: Request, payload: IngestTextRequest) -> IngestTextResponse:
    service = _build_ingest_service(request)
    return await service.ingest_text_items(payload)


@router.post("/files", response_model=IngestFilesResponse)
async def ingest_files(
    request: Request,
    files: list[UploadFile] = File(...),
    source_type: str | None = Form(default=None),
    tags: str | None = Form(default=None),
    metadata: str | None = Form(default=None),
    embedding_profile: str | None = Form(default=None),
    embedding_provider: str | None = Form(default=None),
    embedding_model: str | None = Form(default=None),
    force_reingest: bool = Form(default=False),
) -> IngestFilesResponse:
    service = _build_ingest_service(request)
    normalized_source_type = _normalize_optional_form_value(source_type)
    normalized_embedding_provider = _normalize_optional_form_value(embedding_provider)
    normalized_embedding_model = _normalize_optional_form_value(embedding_model)

    try:
        parsed_tags = _parse_tags(tags)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid tags payload: {exc}",
        ) from exc

    try:
        parsed_metadata = _parse_metadata(metadata)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid metadata payload: {exc}",
        ) from exc

    return await service.ingest_uploaded_files(
        files=files,
        source_type_override=normalized_source_type,
        tags=parsed_tags,
        shared_metadata=parsed_metadata,
        embedding_profile=_normalize_optional_form_value(embedding_profile),
        embedding_provider=normalized_embedding_provider,
        embedding_model=normalized_embedding_model,
        force_reingest=force_reingest,
    )
