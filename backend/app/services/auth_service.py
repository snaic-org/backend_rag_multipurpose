import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
from psycopg_pool import AsyncConnectionPool

from app.core.config import Settings
from app.db.repositories.auth import AuthRepository
from app.models.schemas import (
    AccessTokenResponse,
    ApiKeyCreateResponse,
    ApiKeyResponse,
    AuthenticatedUser,
    UserCreateRequest,
    UserRecord,
    UserResponse,
    UserUpdateRequest,
)


class AuthService:
    def __init__(self, settings: Settings, postgres_pool: AsyncConnectionPool) -> None:
        self._settings = settings
        self._repository = AuthRepository(postgres_pool)

    async def ensure_bootstrap_admin(self) -> None:
        if not self._settings.auth_enabled:
            return

        await self._repository.ensure_auth_tables()
        password_hash = self._hash_password(self._settings.auth_bootstrap_admin_password)
        await self._repository.create_bootstrap_admin_if_missing(
            username=self._settings.auth_bootstrap_admin_username,
            password_hash=password_hash,
        )

    async def issue_access_token(
        self,
        username: str,
        password: str,
    ) -> AccessTokenResponse:
        user = await self._repository.get_user_by_username(username)
        if user is None or not user.is_active:
            raise ValueError("Invalid username or password")

        if not self._verify_password(password, user.password_hash):
            raise ValueError("Invalid username or password")

        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=self._settings.auth_access_token_ttl_seconds
        )
        token = jwt.encode(
            {
                "sub": str(user.id),
                "username": user.username,
                "is_admin": user.is_admin,
                "exp": expires_at,
            },
            self._settings.auth_jwt_secret,
            algorithm=self._settings.auth_jwt_algorithm,
        )

        return AccessTokenResponse(
            access_token=token,
            expires_in_seconds=self._settings.auth_access_token_ttl_seconds,
            user=self._build_authenticated_user(user, auth_type="bearer"),
        )

    async def authenticate_bearer_token(self, token: str) -> AuthenticatedUser:
        try:
            payload = jwt.decode(
                token,
                self._settings.auth_jwt_secret,
                algorithms=[self._settings.auth_jwt_algorithm],
            )
        except jwt.PyJWTError as exc:
            raise ValueError("Invalid or expired bearer token") from exc

        user_id = payload.get("sub")
        if not user_id:
            raise ValueError("Invalid bearer token payload")

        user = await self._repository.get_user_by_id(UUID(user_id))
        if user is None or not user.is_active:
            raise ValueError("Authenticated user is not active")

        return self._build_authenticated_user(user, auth_type="bearer")

    async def authenticate_api_key(self, raw_api_key: str) -> AuthenticatedUser:
        lookup = await self._repository.get_api_key_with_user(self._hash_api_key(raw_api_key))
        if lookup is None:
            raise ValueError("Invalid API key")

        api_key, user = lookup
        if not api_key.is_active or not user.is_active:
            raise ValueError("API key is inactive")

        await self._repository.touch_api_key(api_key.id)
        return self._build_authenticated_user(user, auth_type="api_key")

    async def create_api_key(
        self,
        current_user: AuthenticatedUser,
        name: str,
    ) -> ApiKeyCreateResponse:
        raw_secret = secrets.token_urlsafe(32)
        key_prefix = secrets.token_hex(4)
        raw_api_key = f"rag_{key_prefix}_{raw_secret}"
        record = await self._repository.create_api_key(
            user_id=current_user.id,
            name=name,
            key_prefix=key_prefix,
            key_hash=self._hash_api_key(raw_api_key),
        )
        return ApiKeyCreateResponse(
            api_key=raw_api_key,
            key_prefix=record.key_prefix,
            name=record.name,
            created_at=record.created_at,
        )

    async def list_api_keys_for_user(self, user_id: UUID) -> list[ApiKeyResponse]:
        records = await self._repository.list_api_keys_for_user(user_id)
        return [self._to_api_key_response(record) for record in records]

    async def revoke_api_key(
        self,
        api_key_id: UUID,
        user_id: UUID | None = None,
    ) -> None:
        revoked = await self._repository.revoke_api_key(api_key_id, user_id=user_id)
        if not revoked:
            raise ValueError("API key not found")

    async def create_user(self, payload: UserCreateRequest) -> UserResponse:
        existing = await self._repository.get_user_by_username(payload.username)
        if existing is not None:
            raise ValueError("Username already exists")

        user = await self._repository.create_user(
            username=payload.username,
            password_hash=self._hash_password(payload.password),
            is_active=payload.is_active,
            is_admin=payload.is_admin,
        )
        return self._to_user_response(user)

    async def list_users(self) -> list[UserResponse]:
        users = await self._repository.list_users()
        return [self._to_user_response(user) for user in users]

    async def get_user(self, user_id: UUID) -> UserResponse:
        user = await self._repository.get_user_by_id(user_id)
        if user is None:
            raise ValueError("User not found")
        return self._to_user_response(user)

    async def update_user(
        self,
        user_id: UUID,
        payload: UserUpdateRequest,
        current_user: AuthenticatedUser,
    ) -> UserResponse:
        existing = await self._repository.get_user_by_id(user_id)
        if existing is None:
            raise ValueError("User not found")

        if payload.username and payload.username != existing.username:
            duplicate = await self._repository.get_user_by_username(payload.username)
            if duplicate is not None and duplicate.id != user_id:
                raise ValueError("Username already exists")

        if existing.id == current_user.id and payload.is_admin is False:
            raise ValueError("You cannot remove your own admin access")

        updates: dict[str, object] = {}
        if payload.username is not None:
            updates["username"] = payload.username
        if payload.password is not None:
            updates["password_hash"] = self._hash_password(payload.password)
        if payload.is_active is not None:
            updates["is_active"] = payload.is_active
        if payload.is_admin is not None:
            updates["is_admin"] = payload.is_admin

        updated = await self._repository.update_user(user_id, updates)
        if updated is None:
            raise ValueError("User not found")

        return self._to_user_response(updated)

    async def delete_user(
        self,
        user_id: UUID,
        current_user: AuthenticatedUser,
    ) -> None:
        if user_id == current_user.id:
            raise ValueError("You cannot delete your own account")

        deleted = await self._repository.delete_user(user_id)
        if not deleted:
            raise ValueError("User not found")

    def _build_authenticated_user(
        self,
        user: UserRecord,
        auth_type: str,
    ) -> AuthenticatedUser:
        return AuthenticatedUser(
            id=user.id,
            username=user.username,
            is_admin=user.is_admin,
            auth_type=auth_type,
        )

    def _hash_password(self, password: str) -> str:
        salt = secrets.token_bytes(16)
        derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
        return (
            "scrypt$16384$8$1$"
            f"{base64.b64encode(salt).decode('ascii')}$"
            f"{base64.b64encode(derived).decode('ascii')}"
        )

    def _verify_password(self, password: str, stored_hash: str) -> bool:
        algorithm, n, r, p, encoded_salt, encoded_hash = stored_hash.split("$", maxsplit=5)
        if algorithm != "scrypt":
            raise ValueError("Unsupported password hash algorithm")

        salt = base64.b64decode(encoded_salt.encode("ascii"))
        expected = base64.b64decode(encoded_hash.encode("ascii"))
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
        )
        return hmac.compare_digest(derived, expected)

    def _hash_api_key(self, raw_api_key: str) -> str:
        return hashlib.sha256(raw_api_key.encode("utf-8")).hexdigest()

    def _to_user_response(self, user: UserRecord) -> UserResponse:
        return UserResponse(
            id=user.id,
            username=user.username,
            is_active=user.is_active,
            is_admin=user.is_admin,
            created_at=user.created_at,
            updated_at=user.updated_at,
        )

    def _to_api_key_response(self, record: object) -> ApiKeyResponse:
        return ApiKeyResponse(
            id=record.id,
            user_id=record.user_id,
            name=record.name,
            key_prefix=record.key_prefix,
            is_active=record.is_active,
            last_used_at=record.last_used_at,
            created_at=record.created_at,
        )
