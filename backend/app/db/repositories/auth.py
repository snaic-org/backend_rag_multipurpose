from uuid import UUID, uuid4

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.models.schemas import ApiKeyRecord, UserRecord


class AuthRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def get_user_by_username(self, username: str) -> UserRecord | None:
        query = """
            SELECT
                id,
                username,
                password_hash,
                is_active,
                is_admin,
                created_at,
                updated_at
            FROM app_users
            WHERE username = %(username)s
        """

        async with self._pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(query, {"username": username})
                row = await cursor.fetchone()

        if row is None:
            return None

        return UserRecord.model_validate(row)

    async def get_user_by_id(self, user_id: UUID) -> UserRecord | None:
        query = """
            SELECT
                id,
                username,
                password_hash,
                is_active,
                is_admin,
                created_at,
                updated_at
            FROM app_users
            WHERE id = %(user_id)s
        """

        async with self._pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(query, {"user_id": user_id})
                row = await cursor.fetchone()

        if row is None:
            return None

        return UserRecord.model_validate(row)

    async def create_bootstrap_admin_if_missing(
        self,
        username: str,
        password_hash: str,
    ) -> None:
        existing = await self.get_user_by_username(username)
        if existing is not None:
            return

        query = """
            INSERT INTO app_users (
                id,
                username,
                password_hash,
                is_active,
                is_admin
            )
            VALUES (
                %(id)s,
                %(username)s,
                %(password_hash)s,
                TRUE,
                TRUE
            )
        """

        params = {
            "id": uuid4(),
            "username": username,
            "password_hash": password_hash,
        }

        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(query, params)
            await connection.commit()

    async def create_api_key(
        self,
        user_id: UUID,
        name: str,
        key_prefix: str,
        key_hash: str,
    ) -> ApiKeyRecord:
        query = """
            INSERT INTO api_keys (
                id,
                user_id,
                name,
                key_prefix,
                key_hash,
                is_active
            )
            VALUES (
                %(id)s,
                %(user_id)s,
                %(name)s,
                %(key_prefix)s,
                %(key_hash)s,
                TRUE
            )
            RETURNING
                id,
                user_id,
                name,
                key_prefix,
                key_hash,
                is_active,
                last_used_at,
                created_at
        """

        params = {
            "id": uuid4(),
            "user_id": user_id,
            "name": name,
            "key_prefix": key_prefix,
            "key_hash": key_hash,
        }

        async with self._pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(query, params)
                row = await cursor.fetchone()
            await connection.commit()

        if row is None:
            raise RuntimeError("Failed to create API key.")

        return ApiKeyRecord.model_validate(row)

    async def get_api_key_with_user(self, key_hash: str) -> tuple[ApiKeyRecord, UserRecord] | None:
        query = """
            SELECT
                ak.id AS api_key_id,
                ak.user_id,
                ak.name,
                ak.key_prefix,
                ak.key_hash,
                ak.is_active AS api_key_is_active,
                ak.last_used_at,
                ak.created_at AS api_key_created_at,
                u.id AS user_id_value,
                u.username,
                u.password_hash,
                u.is_active AS user_is_active,
                u.is_admin,
                u.created_at AS user_created_at,
                u.updated_at
            FROM api_keys ak
            JOIN app_users u ON u.id = ak.user_id
            WHERE ak.key_hash = %(key_hash)s
        """

        async with self._pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(query, {"key_hash": key_hash})
                row = await cursor.fetchone()

        if row is None:
            return None

        api_key = ApiKeyRecord(
            id=row["api_key_id"],
            user_id=row["user_id"],
            name=row["name"],
            key_prefix=row["key_prefix"],
            key_hash=row["key_hash"],
            is_active=row["api_key_is_active"],
            last_used_at=row["last_used_at"],
            created_at=row["api_key_created_at"],
        )
        user = UserRecord(
            id=row["user_id_value"],
            username=row["username"],
            password_hash=row["password_hash"],
            is_active=row["user_is_active"],
            is_admin=row["is_admin"],
            created_at=row["user_created_at"],
            updated_at=row["updated_at"],
        )
        return api_key, user

    async def touch_api_key(self, api_key_id: UUID) -> None:
        query = """
            UPDATE api_keys
            SET last_used_at = NOW()
            WHERE id = %(api_key_id)s
        """

        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(query, {"api_key_id": api_key_id})
            await connection.commit()
