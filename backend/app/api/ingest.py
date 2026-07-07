from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status

from app.core.security import require_authenticated_user
from app.models.schemas import (
    AuthenticatedUser,
    CmsIngestRequest,
    IngestFilesResponse,
    IngestTextRequest,
    IngestTextResponse,
    WebsiteIngestRequest,
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


@router.post("/text", response_model=IngestTextResponse)
async def ingest_text(
    request: Request,
    payload: IngestTextRequest,
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
) -> IngestTextResponse:
    service = _build_ingest_service(request)
    return await service.ingest_text_items(payload, current_user=current_user)


@router.post("/files", response_model=IngestFilesResponse)
async def ingest_files(
    request: Request,
    files: list[UploadFile] = File(...),
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
) -> IngestFilesResponse:
    service = _build_ingest_service(request)
    return await service.ingest_uploaded_files(
        files=files,
        current_user=current_user,
    )


@router.post(
    "/websites",
    response_model=IngestFilesResponse,
    summary="Scrape and ingest one or more website URLs",
    description=(
        "Fetches each URL, extracts the readable page content (stripping scripts, "
        "navigation, headers/footers), chunks and embeds it, then stores it for retrieval.\n\n"
        "- Pass a single URL or up to 50 URLs in the `urls` list.\n"
        "- Each URL is processed independently: one failing URL does not abort the batch. "
        "Per-URL outcomes (success, deduplicated, or error) are returned in `results`.\n"
        "- Set `force_reingest: true` to re-scrape and replace a URL that was ingested before.\n\n"
        "Common per-URL errors returned in `results[].error`: request timeout, unreachable host, "
        "HTTP error status, non-HTML content, error page detected, or insufficient page content."
    ),
)
async def ingest_websites(
    request: Request,
    payload: WebsiteIngestRequest,
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
) -> IngestFilesResponse:
    service = _build_ingest_service(request)
    return await service.ingest_websites(payload, current_user=current_user)


@router.post(
    "/cms",
    response_model=IngestFilesResponse,
    summary="Ingest published content from the Contentful CMS",
    description=(
        "Pulls published content directly from the Contentful Content Delivery API and ingests it. "
        "This is the recommended way to index the SNAIC/SIT site content, because the public pages "
        "(snaic.net) are JavaScript-rendered and cannot be reliably scraped as HTML.\n\n"
        "- Send an empty `content_types` list to ingest all configured defaults: "
        "**Projects, News, Publications, Team**.\n"
        "- Or pass specific Contentful content_type ids (e.g. `[\"team\", \"publications\"]`).\n"
        "- `force_reingest` defaults to `true` so CMS edits replace previously indexed entries.\n\n"
        "Each entry's rich-text and text fields are flattened, and the citation URL points to the "
        "corresponding public page. Requires `CONTENTFUL_SPACE_ID` and `CONTENTFUL_DELIVERY_TOKEN` "
        "to be configured on the server."
    ),
)
async def ingest_cms(
    request: Request,
    payload: CmsIngestRequest,
    current_user: AuthenticatedUser = Depends(require_authenticated_user),
) -> IngestFilesResponse:
    service = _build_ingest_service(request)
    try:
        return await service.ingest_cms(payload, current_user=current_user)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
