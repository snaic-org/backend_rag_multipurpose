import hashlib
from pathlib import Path
from uuid import UUID

INGEST_EMBEDDING_BATCH_SIZE = 50

import httpx
from fastapi import UploadFile
from psycopg_pool import AsyncConnectionPool

from app.core.config import Settings
from app.core.config import EMBEDDING_CACHE_TTL_SECONDS
from app.core.config import (
    CONTENTFUL_CDN_BASE,
    CONTENTFUL_SKIP_FIELDS,
    DEFAULT_CONTENTFUL_CONTENT_TYPES,
)
from app.core.logging import get_logger
from app.db.qdrant import QdrantManager
from app.db.redis import RedisManager
from app.db.repositories.chunks import ChunkRepository
from app.db.repositories.documents import DocumentRepository
from app.models.schemas import (
    AuthenticatedUser,
    CmsIngestRequest,
    IngestFileResult,
    IngestFilesResponse,
    IngestTextRequest,
    IngestTextResponse,
    NormalizedDocument,
    ParsedFile,
    WebsiteIngestRequest,
)
from app.parsers.factory import ParserFactory
from app.providers.registry import ProviderRegistry
from app.services.model_selection_service import ModelSelectionService
from app.services.cache_service import CacheService
from app.services.chunking import ChunkingService
from app.services.embeddings import EmbeddingService

logger = get_logger(__name__)


def _contentful_richtext_to_text(node) -> str:
    """Recursively flatten a Contentful rich-text document node into plain text."""
    if not isinstance(node, dict):
        return ""
    if node.get("nodeType") == "text":
        return node.get("value", "")
    parts = [_contentful_richtext_to_text(child) for child in node.get("content", [])]
    return " ".join(p for p in parts if p).strip()


class IngestService:
    def __init__(
        self,
        settings: Settings,
        redis_manager: RedisManager,
        qdrant_manager: QdrantManager,
        postgres_pool: AsyncConnectionPool,
        provider_registry: ProviderRegistry,
        model_selection_service: ModelSelectionService,
    ) -> None:
        self._settings = settings
        self._redis_manager = redis_manager
        self._postgres_pool = postgres_pool
        self._provider_registry = provider_registry
        self._model_selection_service = model_selection_service
        self._document_repository = DocumentRepository(postgres_pool)
        self._chunk_repository = ChunkRepository(qdrant_manager)
        self._parser_factory = ParserFactory()
        self._chunking_service = ChunkingService(settings)
        self._embedding_service = EmbeddingService(
            settings,
            cache_service=CacheService(
                redis_manager.client,
                ttl_seconds=EMBEDDING_CACHE_TTL_SECONDS,
            ),
        )

    async def ingest_text_items(
        self,
        payload: IngestTextRequest,
        current_user: AuthenticatedUser,
    ) -> IngestTextResponse:
        default_embedding_profile = await self._model_selection_service.get_embedding_profile_name()
        selection = self._embedding_service.resolve_selection(
            None,
            None,
            None,
            default_profile_name=default_embedding_profile,
        )

        results: list[IngestFileResult] = []
        documents_inserted = 0
        chunks_inserted = 0

        for item in payload.items:
            document = NormalizedDocument(
                title=item.title,
                source_type="text",
                content=item.content.strip(),
                metadata=self._build_system_metadata(
                    current_user=current_user,
                    source_kind="text",
                    title=item.title,
                ),
            )

            persisted = await self._persist_documents(
                parsed_file=ParsedFile(
                    filename=item.title,
                    detected_type="text",
                    documents=[document],
                ),
                embedding_provider=selection.provider,
                embedding_model=selection.model,
                embedding_profile=selection.profile_name,
                embedding_dimension=selection.dimension,
                force_reingest=False,
                created_by=current_user.username,
            )
            results.extend(persisted["results"])
            documents_inserted += persisted["documents_inserted"]
            chunks_inserted += persisted["chunks_inserted"]

        return IngestTextResponse(
            documents_inserted=documents_inserted,
            chunks_inserted=chunks_inserted,
            embedding_provider=selection.provider,
            embedding_model=selection.model,
            results=results,
        )

    async def ingest_uploaded_files(
        self,
        files: list[UploadFile],
        current_user: AuthenticatedUser,
    ) -> IngestFilesResponse:
        default_embedding_profile = await self._model_selection_service.get_embedding_profile_name()
        selection = self._embedding_service.resolve_selection(
            None,
            None,
            None,
            default_profile_name=default_embedding_profile,
        )

        results: list[IngestFileResult] = []
        total_chunks_inserted = 0

        for upload in files:
            try:
                content = await upload.read()
                if not content or not content.strip():
                    raise ValueError("empty file")

                filename = upload.filename or "upload"
                detected_type = self._parser_factory.detect_type(filename, upload.content_type)
                parser = self._parser_factory.get_parser(detected_type)
                parsed_file = await parser.parse(
                    filename=filename,
                    content=content,
                    mime_type=upload.content_type,
                    shared_metadata=self._build_system_metadata(
                        current_user=current_user,
                        source_kind="file",
                        title=filename,
                        original_filename=filename,
                        mime_type=upload.content_type,
                    ),
                )

                persisted = await self._persist_documents(
                    parsed_file=parsed_file,
                    embedding_provider=selection.provider,
                    embedding_model=selection.model,
                    embedding_profile=selection.profile_name,
                    embedding_dimension=selection.dimension,
                    force_reingest=False,
                    created_by=current_user.username,
                )
                results.extend(persisted["results"])
                total_chunks_inserted += persisted["chunks_inserted"]
            except Exception as exc:
                logger.exception("file_ingest_failed filename=%s", upload.filename)
                results.append(
                    IngestFileResult(
                        filename=upload.filename or "unknown",
                        detected_type=Path(upload.filename or "").suffix.lstrip(".") or "unknown",
                        success=False,
                        chunks_created=0,
                        error=str(exc),
                    )
                )

        succeeded = sum(1 for result in results if result.success)
        failed = sum(1 for result in results if not result.success)

        return IngestFilesResponse(
            total_files=len(files),
            succeeded=succeeded,
            failed=failed,
            total_chunks_inserted=total_chunks_inserted,
            embedding_provider=selection.provider,
            embedding_model=selection.model,
            results=results,
        )

    async def _persist_documents(
        self,
        parsed_file: ParsedFile,
        embedding_provider: str,
        embedding_model: str,
        embedding_profile: str,
        embedding_dimension: int,
        force_reingest: bool,
        created_by: str = "system",
    ) -> dict:
        results: list[IngestFileResult] = []
        documents_inserted = 0
        chunks_inserted = 0

        # Phase 1: dedup check and chunk prep for all documents
        new_documents: list[tuple] = []
        for document in parsed_file.documents:
            content_hash = self._hash_document(document.content)
            if force_reingest:
                existing = await self._document_repository.get_by_content_hash(
                    content_hash=content_hash,
                    embedding_provider=embedding_provider,
                    embedding_model=embedding_model,
                )
                if existing is not None:
                    await self._chunk_repository.delete_for_document(existing.id, embedding_dimension)
                    await self._document_repository.delete_by_id(existing.id)

            document_record, created = await self._document_repository.create_or_get_by_content_hash(
                document=document,
                embedding_provider=embedding_provider,
                embedding_model=embedding_model,
                content_hash=content_hash,
                created_by=created_by,
            )
            if not created:
                results.append(
                    IngestFileResult(
                        filename=parsed_file.filename,
                        detected_type=parsed_file.detected_type,
                        success=True,
                        chunks_created=0,
                        deduplicated=True,
                        document_id=document_record.id,
                    )
                )
                continue

            chunks = self._chunking_service.build_chunks(document)
            if not chunks:
                results.append(
                    IngestFileResult(
                        filename=parsed_file.filename,
                        detected_type=parsed_file.detected_type,
                        success=False,
                        chunks_created=0,
                        error="no chunks generated from content",
                    )
                )
                continue

            new_documents.append((document, document_record, chunks))

        if not new_documents:
            return {"results": results, "documents_inserted": documents_inserted, "chunks_inserted": chunks_inserted}

        # Phase 2: batch embed all chunks in groups to stay within provider limits
        all_texts = [chunk["content"] for _, _, chunks in new_documents for chunk in chunks]
        all_embeddings: list[list[float]] = []
        for i in range(0, len(all_texts), INGEST_EMBEDDING_BATCH_SIZE):
            batch = all_texts[i:i + INGEST_EMBEDDING_BATCH_SIZE]
            _, batch_embeddings = await self._embedding_service.embed_texts(
                texts=batch,
                provider=embedding_provider,
                model=embedding_model,
                input_type="passage",
            )
            all_embeddings.extend(batch_embeddings)

        # Phase 3: slice embeddings per document and upsert
        embed_offset = 0
        for document, document_record, chunks in new_documents:
            doc_embeddings = all_embeddings[embed_offset:embed_offset + len(chunks)]
            embed_offset += len(chunks)

            chunk_upserts = self._chunking_service.build_chunk_upserts(document, doc_embeddings)
            inserted_chunks = await self._chunk_repository.bulk_create(
                document_id=document_record.id,
                chunks=chunk_upserts,
                embedding_provider=embedding_provider,
                embedding_model=embedding_model,
                embedding_profile=embedding_profile,
                embedding_dimension=embedding_dimension,
            )

            documents_inserted += 1
            chunks_inserted += len(inserted_chunks)
            results.append(
                IngestFileResult(
                    filename=parsed_file.filename,
                    detected_type=parsed_file.detected_type,
                    success=True,
                    chunks_created=len(inserted_chunks),
                    document_id=document_record.id,
                )
            )

        return {
            "results": results,
            "documents_inserted": documents_inserted,
            "chunks_inserted": chunks_inserted,
        }

    def _hash_document(self, content: str) -> str:
        normalized = " ".join(content.split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    async def ingest_websites(
        self,
        payload: WebsiteIngestRequest,
        current_user: AuthenticatedUser,
    ) -> IngestFilesResponse:
        default_embedding_profile = await self._model_selection_service.get_embedding_profile_name()
        selection = self._embedding_service.resolve_selection(
            None, None, None, default_profile_name=default_embedding_profile,
        )

        all_results: list[IngestFileResult] = []
        total_chunks = 0
        parser = self._parser_factory.get_parser("website")

        for url in payload.urls:
            result = await self._ingest_single_url(
                url=url,
                force_reingest=payload.force_reingest,
                selection=selection,
                parser=parser,
                current_user=current_user,
            )
            all_results.extend(result["results"])
            total_chunks += result["chunks_inserted"]

        return IngestFilesResponse(
            total_files=len(payload.urls),
            succeeded=sum(1 for r in all_results if r.success),
            failed=sum(1 for r in all_results if not r.success),
            total_chunks_inserted=total_chunks,
            embedding_provider=selection.provider,
            embedding_model=selection.model,
            results=all_results,
        )

    async def _ingest_single_url(
        self,
        url: str,
        force_reingest: bool,
        selection,
        parser,
        current_user: AuthenticatedUser,
    ) -> dict:
        def _fail(error: str) -> dict:
            return {
                "results": [IngestFileResult(
                    filename=url,
                    detected_type="website",
                    success=False,
                    chunks_created=0,
                    error=error,
                )],
                "chunks_inserted": 0,
            }

        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
                response = await client.get(
                    url,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; RAGBot/1.0)"},
                )
                response.raise_for_status()
        except httpx.TimeoutException:
            return _fail("Request timed out after 60 seconds. The site may be slow or unreachable.")
        except httpx.TooManyRedirects:
            return _fail("Too many redirects. Check the URL is correct and not in a redirect loop.")
        except httpx.ConnectError:
            return _fail("Could not connect to the host. Check the URL and that the site is publicly accessible.")
        except httpx.HTTPStatusError as exc:
            return _fail(f"HTTP {exc.response.status_code}: site returned an error response.")
        except httpx.InvalidURL:
            return _fail("Invalid URL format.")
        except Exception as exc:
            return _fail(f"Unexpected fetch error: {exc}")

        shared_metadata = self._build_system_metadata(
            current_user=current_user,
            source_kind="website",
            title=url,
        )
        shared_metadata["source_url"] = url

        try:
            parsed_file = await parser.parse(
                filename=url,
                content=response.content,
                mime_type=response.headers.get("content-type"),
                shared_metadata=shared_metadata,
            )
        except ValueError as exc:
            return _fail(str(exc))
        except Exception as exc:
            return _fail(f"Failed to parse page content: {exc}")

        for doc in parsed_file.documents:
            doc.url = url

        try:
            persisted = await self._persist_documents(
                parsed_file=parsed_file,
                embedding_provider=selection.provider,
                embedding_model=selection.model,
                embedding_profile=selection.profile_name,
                embedding_dimension=selection.dimension,
                force_reingest=force_reingest,
                created_by=current_user.username,
            )
        except Exception as exc:
            return _fail(f"Failed to embed and store content: {exc}")

        return persisted

    async def ingest_cms(
        self,
        payload: CmsIngestRequest,
        current_user: AuthenticatedUser,
    ) -> IngestFilesResponse:
        space = (self._settings.contentful_space_id or "").strip()
        token = (self._settings.contentful_delivery_token or "").strip()
        if not space or not token:
            raise ValueError(
                "Contentful is not configured. Set CONTENTFUL_SPACE_ID and "
                "CONTENTFUL_DELIVERY_TOKEN environment variables."
            )
        env = (self._settings.contentful_environment or "master").strip()
        base = self._settings.contentful_site_base_url.rstrip("/")

        configs = self._resolve_cms_content_types(payload.content_types)
        if not configs:
            raise ValueError("No matching content types to ingest.")

        default_embedding_profile = await self._model_selection_service.get_embedding_profile_name()
        selection = self._embedding_service.resolve_selection(
            None, None, None, default_profile_name=default_embedding_profile,
        )

        all_results: list[IngestFileResult] = []
        total_chunks = 0

        # Clean up before a forced re-ingest so stale/duplicate/wrong-type CMS docs
        # do not linger. A full refresh (no specific content_types requested) wipes
        # ALL existing CMS docs for a pristine rebuild. A targeted re-ingest only
        # clears the specific content types being refreshed.
        purged = 0
        if payload.force_reingest:
            if not payload.content_types:
                cms_ids = await self._document_repository.list_ids_by_source_type("cms")
                for doc_id in cms_ids:
                    await self._chunk_repository.delete_for_document(doc_id, selection.dimension)
                    await self._document_repository.delete_by_id(doc_id)
                purged = len(cms_ids)
                logger.info("cms_full_refresh_purged_documents count=%d", purged)

        async with httpx.AsyncClient(timeout=60.0) as client:
            for cfg in configs:
                content_type = cfg["content_type"]
                label = cfg["label"]

                # For a targeted re-ingest, clear just this content type up front so
                # entries whose text changed are replaced rather than duplicated.
                if payload.force_reingest and payload.content_types:
                    await self._delete_cms_documents(content_type, selection.dimension)

                try:
                    entries, includes = await self._fetch_contentful_entries(
                        client, space, env, token, content_type,
                    )
                except httpx.HTTPStatusError as exc:
                    all_results.append(IngestFileResult(
                        filename=f"contentful:{content_type}",
                        detected_type="cms",
                        success=False,
                        chunks_created=0,
                        error=f"Contentful returned HTTP {exc.response.status_code}. Check space id, token, and content type id.",
                    ))
                    continue
                except Exception as exc:
                    all_results.append(IngestFileResult(
                        filename=f"contentful:{content_type}",
                        detected_type="cms",
                        success=False,
                        chunks_created=0,
                        error=f"Failed to fetch from Contentful: {exc}",
                    ))
                    continue

                documents: list[NormalizedDocument] = []
                for entry in entries:
                    doc = self._contentful_entry_to_document(entry, includes, cfg, base, current_user)
                    if doc is not None:
                        documents.append(doc)

                # Add a consolidated roster/index document so aggregation questions
                # ("who are the leaders / team / list all X") can be answered from a
                # single retrieval hit instead of needing every individual entry.
                if cfg.get("roster") and entries:
                    roster = self._build_cms_roster_document(cfg, entries, base, current_user)
                    if roster is not None:
                        documents.append(roster)

                if not documents:
                    all_results.append(IngestFileResult(
                        filename=f"contentful:{content_type}",
                        detected_type="cms",
                        success=False,
                        chunks_created=0,
                        error="No entries with usable text content were found for this content type.",
                    ))
                    continue

                persisted = await self._persist_documents(
                    parsed_file=ParsedFile(
                        filename=f"{label} ({content_type})",
                        detected_type="cms",
                        documents=documents,
                    ),
                    embedding_provider=selection.provider,
                    embedding_model=selection.model,
                    embedding_profile=selection.profile_name,
                    embedding_dimension=selection.dimension,
                    force_reingest=payload.force_reingest,
                    created_by=current_user.username,
                )
                all_results.extend(persisted["results"])
                total_chunks += persisted["chunks_inserted"]

        return IngestFilesResponse(
            total_files=len(all_results),
            succeeded=sum(1 for r in all_results if r.success),
            failed=sum(1 for r in all_results if not r.success),
            total_chunks_inserted=total_chunks,
            embedding_provider=selection.provider,
            embedding_model=selection.model,
            results=all_results,
        )

    async def _delete_cms_documents(self, content_type: str, embedding_dimension: int) -> int:
        doc_ids = await self._document_repository.list_ids_by_content_type(content_type)
        for doc_id in doc_ids:
            await self._chunk_repository.delete_for_document(doc_id, embedding_dimension)
            await self._document_repository.delete_by_id(doc_id)
        return len(doc_ids)

    def _resolve_cms_content_types(self, requested: list[str]) -> list[dict]:
        if not requested:
            return list(DEFAULT_CONTENTFUL_CONTENT_TYPES)

        known = {c["content_type"]: c for c in DEFAULT_CONTENTFUL_CONTENT_TYPES}
        resolved: list[dict] = []
        for content_type in requested:
            if content_type in known:
                resolved.append(known[content_type])
            else:
                resolved.append({
                    "content_type": content_type,
                    "label": content_type,
                    "url_path": "",
                    "detail_page": False,
                })
        return resolved

    async def _fetch_contentful_entries(
        self,
        client: httpx.AsyncClient,
        space: str,
        env: str,
        token: str,
        content_type: str,
    ) -> tuple[list[dict], dict]:
        url = f"{CONTENTFUL_CDN_BASE}/spaces/{space}/environments/{env}/entries"
        all_items: list[dict] = []
        includes: dict = {"Entry": [], "Asset": []}
        skip = 0
        limit = 100

        while True:
            params = {
                "content_type": content_type,
                "access_token": token,
                "limit": limit,
                "skip": skip,
                "include": 1,
            }
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            items = data.get("items", [])
            all_items.extend(items)

            inc = data.get("includes", {})
            includes["Entry"].extend(inc.get("Entry", []))
            includes["Asset"].extend(inc.get("Asset", []))

            total = data.get("total", 0)
            skip += limit
            if not items or skip >= total:
                break

        return all_items, includes

    def _contentful_entry_to_document(
        self,
        entry: dict,
        includes: dict,
        cfg: dict,
        base: str,
        current_user: AuthenticatedUser,
    ) -> NormalizedDocument | None:
        fields = entry.get("fields", {})
        if not fields:
            return None

        entry_id = entry.get("sys", {}).get("id", "")
        content_type = cfg["content_type"]
        is_team = content_type == "team"

        # For people, the document title should be the person's NAME, not their
        # job title. For everything else the entry title/heading is correct.
        name = self._cf_field(fields, "name", "fullName")
        role = self._cf_field(fields, "title", "role", "position")
        member_type = self._cf_field(fields, "memberType", "category", "group")

        if is_team and name:
            title = name
        else:
            title = self._contentful_entry_title(fields, cfg["label"])

        body = self._contentful_entry_text(entry, includes)
        if not body.strip():
            return None

        # Prepend framing so every entry contains the "SNAIC" anchor and the key
        # descriptors people actually search for (name, role, member type).
        frame_lines = [cfg.get("frame", f"This is SNAIC {cfg['label']} content.")]
        if is_team:
            descriptor = ", ".join(part for part in (name, role) if part)
            if descriptor:
                frame_lines.append(f"{descriptor}.")
            if member_type:
                frame_lines.append(f"Member type at SNAIC: {member_type}.")
        header = " ".join(frame_lines)

        content = f"{header}\n\n{body}".strip()

        url = base + cfg.get("url_path", "")
        if cfg.get("detail_page") and entry_id:
            url = f"{url}/{entry_id}"

        metadata = self._build_system_metadata(
            current_user=current_user,
            source_kind="cms",
            title=title,
        )
        metadata["source_url"] = url
        metadata["contentful_content_type"] = content_type
        metadata["contentful_entry_id"] = entry_id
        if member_type:
            metadata["member_type"] = member_type

        return NormalizedDocument(
            title=title,
            source_type="cms",
            content=content,
            metadata=metadata,
            url=url,
        )

    def _build_cms_roster_document(
        self,
        cfg: dict,
        entries: list[dict],
        base: str,
        current_user: AuthenticatedUser,
    ) -> NormalizedDocument | None:
        content_type = cfg["content_type"]
        url = base + cfg.get("url_path", "")

        if content_type == "team":
            # Group members by MemberType, listing leadership groups first.
            groups: dict[str, list[str]] = {}
            for entry in entries:
                fields = entry.get("fields", {})
                name = self._cf_field(fields, "name", "fullName") or "Unknown"
                role = self._cf_field(fields, "title", "role", "position")
                member_type = self._cf_field(fields, "memberType", "category", "group") or "Member"
                line = f"- {name}" + (f" - {role}" if role else "")
                groups.setdefault(member_type, []).append(line)

            def group_priority(member_type: str) -> tuple[int, str]:
                lowered = member_type.lower()
                if any(word in lowered for word in ("director", "lead", "head", "chair", "advis", "principal")):
                    return (0, member_type)
                return (1, member_type)

            lines = [
                "SNAIC (SIT x NVIDIA AI Centre) - Team and Leadership Directory.",
                "The following people make up the SNAIC team, grouped by their role. "
                "Directors and leadership are listed first.",
                "",
            ]
            for member_type in sorted(groups, key=group_priority):
                heading = member_type if member_type.endswith("s") else f"{member_type}s"
                lines.append(f"{heading}:")
                lines.extend(groups[member_type])
                lines.append("")
            content = "\n".join(lines).strip()
            title = "SNAIC Team and Leadership Directory"
        else:
            entry_lines = []
            for entry in entries:
                fields = entry.get("fields", {})
                t = self._contentful_entry_title(fields, "")
                if not t:
                    continue
                summary = self._cf_field(fields, "summary", "brief", "description")
                if summary:
                    summary = " ".join(summary.split())[:200]
                    entry_lines.append(f"- {t}: {summary}")
                else:
                    entry_lines.append(f"- {t}")
            if not entry_lines:
                return None
            content = "\n".join(
                [f"Complete list of all SNAIC (SIT x NVIDIA AI Centre) {cfg['label']}:", ""] + entry_lines
            ).strip()
            title = f"SNAIC {cfg['label']} Directory"

        metadata = self._build_system_metadata(
            current_user=current_user,
            source_kind="cms",
            title=title,
        )
        metadata["source_url"] = url
        metadata["contentful_content_type"] = content_type
        metadata["cms_roster"] = True

        return NormalizedDocument(
            title=title,
            source_type="cms",
            content=content,
            metadata=metadata,
            url=url,
        )

    @staticmethod
    def _cf_field(fields: dict, *names: str) -> str:
        """Case-insensitive lookup returning the first matching string field."""
        lowered = {k.lower(): v for k, v in fields.items()}
        for name in names:
            value = lowered.get(name.lower())
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @staticmethod
    def _contentful_entry_title(fields: dict, fallback: str) -> str:
        for key in ("title", "name", "heading", "projectTitle", "newsTitle", "fullName", "publicationTitle"):
            for actual_key, value in fields.items():
                if actual_key.lower() == key and isinstance(value, str) and value.strip():
                    return value.strip()
        return fallback

    def _contentful_entry_text(self, entry_or_asset: dict, includes: dict, depth: int = 0) -> str:
        fields = entry_or_asset.get("fields", {})
        lines: list[str] = []
        for key, value in fields.items():
            if key.lower() in CONTENTFUL_SKIP_FIELDS:
                continue
            text = self._contentful_field_to_text(value, includes, depth)
            if text and text.strip():
                label = key[0].upper() + key[1:]
                lines.append(f"{label}: {text.strip()}")
        return "\n".join(lines)

    def _contentful_field_to_text(self, value, includes: dict, depth: int = 0) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, bool):
            return "yes" if value else "no"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, list):
            parts = [self._contentful_field_to_text(v, includes, depth) for v in value]
            return "\n".join(p for p in parts if p and p.strip())
        if isinstance(value, dict):
            # Contentful rich text document
            if value.get("nodeType") == "document":
                return _contentful_richtext_to_text(value)
            sys_info = value.get("sys", {})
            if sys_info.get("type") == "Link":
                if depth < 1:
                    resolved = self._resolve_contentful_link(sys_info, includes)
                    if resolved is not None:
                        return self._contentful_entry_text(resolved, includes, depth + 1)
                return ""
            # Plain JSON object field (e.g. a project's techStack) - flatten it.
            if not sys_info:
                parts = []
                for sub_key, sub_value in value.items():
                    sub_text = self._contentful_field_to_text(sub_value, includes, depth)
                    if sub_text and sub_text.strip():
                        parts.append(f"{sub_key}: {sub_text.strip()}")
                return "; ".join(parts)
        return ""

    @staticmethod
    def _resolve_contentful_link(link_sys: dict, includes: dict) -> dict | None:
        link_type = link_sys.get("linkType")
        link_id = link_sys.get("id")
        for item in includes.get(link_type, []):
            if item.get("sys", {}).get("id") == link_id:
                return item
        return None

    async def delete_document(self, document_id: UUID) -> bool:
        doc = await self._document_repository.get_by_id(document_id)
        if doc is None:
            return False

        default_embedding_profile = await self._model_selection_service.get_embedding_profile_name()
        selection = self._embedding_service.resolve_selection(
            None, None, None, default_profile_name=default_embedding_profile,
        )

        await self._chunk_repository.delete_for_document(document_id, selection.dimension)
        await self._document_repository.delete_by_id(document_id)
        return True

    def _build_system_metadata(
        self,
        *,
        current_user: AuthenticatedUser,
        source_kind: str,
        title: str,
        original_filename: str | None = None,
        mime_type: str | None = None,
    ) -> dict:
        metadata = {
            "source_kind": source_kind,
            "title": title,
            "created_by": current_user.username,
        }
        if original_filename is not None:
            metadata["original_filename"] = original_filename
        if mime_type is not None:
            metadata["mime_type"] = mime_type
        return metadata
