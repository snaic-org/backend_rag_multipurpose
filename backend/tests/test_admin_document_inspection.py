from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

import asyncio


import httpx
import jwt
from fastapi import FastAPI

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.models.schemas import (
    AccessTokenResponse,
    AuthenticatedUser,
    ChunkRecord,
    DocumentRecord,
    IngestedDocumentDetails,
    IngestedDocumentSummary,
)


class StubSettings:
    app_name = "backend-rag-multipurpose"
    auth_enabled = True
    auth_require_https = False
    auth_jwt_secret = "test-secret"
    auth_jwt_algorithm = "HS256"
    auth_access_token_ttl_seconds = 3600
    auth_bootstrap_admin_username = "admin"
    auth_bootstrap_admin_password = "change-me-immediately"
    embedding_profiles = {
        "openai_small_1536": SimpleNamespace(provider="openai", model="text-embedding-3-small", dimension=1536),
    }


class FakeAuthService:
    def __init__(self, settings: StubSettings) -> None:
        self._settings = settings
        self._user = AuthenticatedUser(
            id=UUID("11111111-1111-1111-1111-111111111111"),
            username=settings.auth_bootstrap_admin_username,
            is_admin=True,
            auth_type="bearer",
        )

    async def issue_access_token(self, username: str, password: str) -> AccessTokenResponse:
        if (
            username != self._settings.auth_bootstrap_admin_username
            or password != self._settings.auth_bootstrap_admin_password
        ):
            raise ValueError("Invalid username or password")

        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=self._settings.auth_access_token_ttl_seconds
        )
        token = jwt.encode(
            {
                "sub": str(self._user.id),
                "username": self._user.username,
                "is_admin": self._user.is_admin,
                "exp": expires_at,
            },
            self._settings.auth_jwt_secret,
            algorithm=self._settings.auth_jwt_algorithm,
        )
        return AccessTokenResponse(
            access_token=token,
            expires_in_seconds=self._settings.auth_access_token_ttl_seconds,
            user=self._user,
        )

    async def authenticate_bearer_token(self, token: str) -> AuthenticatedUser:
        payload = jwt.decode(
            token,
            self._settings.auth_jwt_secret,
            algorithms=[self._settings.auth_jwt_algorithm],
        )
        if payload.get("sub") != str(self._user.id):
            raise ValueError("Invalid bearer token payload")
        return self._user

class FakeDocumentInspectionService:
    def __init__(self) -> None:
        self._document = DocumentRecord(
            id=UUID("22222222-2222-2222-2222-222222222222"),
            content_hash="hash",
            title="SNAIC Overview",
            url=None,
            source_type="docx",
            metadata={"department": "AI"},
            original_filename="SNAIC Overview.docx",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            created_by="admin",
            embedding_provider="openai",
            embedding_model="text-embedding-3-small",
            created_at=datetime(2026, 3, 26, tzinfo=timezone.utc),
            updated_at=datetime(2026, 3, 26, tzinfo=timezone.utc),
        )
        self._chunks = [
            ChunkRecord(
                id=UUID("33333333-3333-3333-3333-333333333331"),
                document_id=self._document.id,
                chunk_index=0,
                content="Title: SNAIC Overview\nSection: Collaboration Models with SNAIC\nStep 1: Pre-engagement questionnaire.",
                metadata={"section_title": "Collaboration Models with SNAIC"},
                embedding_provider="openai",
                embedding_model="text-embedding-3-small",
                embedding_profile="openai_small_1536",
                created_at=datetime(2026, 3, 26, tzinfo=timezone.utc),
            ),
            ChunkRecord(
                id=UUID("33333333-3333-3333-3333-333333333332"),
                document_id=self._document.id,
                chunk_index=1,
                content="Title: SNAIC Overview\nSection: Collaboration Models with SNAIC\nStep 2: One-to-one consultation.",
                metadata={"section_title": "Collaboration Models with SNAIC"},
                embedding_provider="openai",
                embedding_model="text-embedding-3-small",
                embedding_profile="openai_small_1536",
                created_at=datetime(2026, 3, 26, tzinfo=timezone.utc),
            ),
        ]

    async def list_documents(self, limit: int = 20, source_type: str | None = None) -> list[IngestedDocumentSummary]:
        return [IngestedDocumentSummary(document=self._document, embedding_profile="openai_small_1536")]

    async def get_document(self, document_id: UUID) -> IngestedDocumentDetails:
        if document_id != self._document.id:
            raise ValueError("document not found")
        return IngestedDocumentDetails(
            document=self._document,
            embedding_profile="openai_small_1536",
            full_text="\n\n".join(chunk.content for chunk in self._chunks),
            chunk_count=len(self._chunks),
            chunks=self._chunks,
        )

    async def get_document_chunks(self, document_id: UUID) -> list[ChunkRecord]:
        if document_id != self._document.id:
            raise ValueError("document not found")
        return self._chunks


def build_app() -> FastAPI:
    settings = StubSettings()
    app = FastAPI()
    app.include_router(auth_router, prefix="/auth")
    app.include_router(admin_router, prefix="/admin")
    app.state.settings = settings
    app.state.auth_service = FakeAuthService(settings)
    app.state.document_inspection_service = FakeDocumentInspectionService()
    return app


def test_admin_can_list_and_view_ingested_document(monkeypatch) -> None:
    app = build_app()

    monkeypatch.setattr(
        "app.api.admin._build_document_inspection_service",
        lambda request: request.app.state.document_inspection_service,
    )

    async def run_flow() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            token_response = await client.post(
                "/auth/token",
                json={
                    "username": "admin",
                    "password": "change-me-immediately",
                },
            )
            assert token_response.status_code == 200, token_response.text
            token = token_response.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            list_response = await client.get("/admin/documents", headers=headers)
            assert list_response.status_code == 200, list_response.text
            documents = list_response.json()
            assert documents[0]["document"]["title"] == "SNAIC Overview"
            assert documents[0]["embedding_profile"] == "openai_small_1536"

            detail_response = await client.get(
                f"/admin/documents/{app.state.document_inspection_service._document.id}",
                headers=headers,
            )
            assert detail_response.status_code == 200, detail_response.text
            detail = detail_response.json()
            assert detail["document"]["original_filename"] == "SNAIC Overview.docx"
            assert detail["chunk_count"] == 2
            assert "Step 1" in detail["full_text"]
            assert len(detail["chunks"]) == 2

            raw_response = await client.get(
                f"/admin/documents/{app.state.document_inspection_service._document.id}/raw",
                headers=headers,
            )
            assert raw_response.status_code == 200, raw_response.text
            raw_chunks = raw_response.json()
            assert len(raw_chunks) == 2
            assert raw_chunks[0]["metadata"]["section_title"] == "Collaboration Models with SNAIC"

    asyncio.run(run_flow())
