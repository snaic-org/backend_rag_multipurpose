from uuid import UUID, uuid4

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.models.schemas import ApiKeyRecord, UserRecord


class AuthRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def ensure_auth_tables(self) -> None:
        queries = [
            """
            CREATE TABLE IF NOT EXISTS app_users (
                id UUID PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                is_admin BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS api_keys (
                id UUID PRIMARY KEY,
                user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                key_prefix TEXT NOT NULL,
                key_hash TEXT NOT NULL UNIQUE,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                last_used_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_api_keys_user_id ON api_keys (user_id)",
            "CREATE INDEX IF NOT EXISTS idx_api_keys_prefix ON api_keys (key_prefix)",
        ]

        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                for query in queries:
                    await cursor.execute(query)
            await connection.commit()

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

    async def create_user(
        self,
        username: str,
        password_hash: str,
        is_active: bool,
        is_admin: bool,
    ) -> UserRecord:
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
                %(is_active)s,
                %(is_admin)s
            )
            RETURNING
                id,
                username,
                password_hash,
                is_active,
                is_admin,
                created_at,
                updated_at
        """

        params = {
            "id": uuid4(),
            "username": username,
            "password_hash": password_hash,
            "is_active": is_active,
            "is_admin": is_admin,
        }

        async with self._pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(query, params)
                row = await cursor.fetchone()
            await connection.commit()

        if row is None:
            raise RuntimeError("Failed to create user.")

        return UserRecord.model_validate(row)

    async def list_users(self) -> list[UserRecord]:
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
            ORDER BY created_at ASC
        """

        async with self._pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(query)
                rows = await cursor.fetchall()

        return [UserRecord.model_validate(row) for row in rows]

    async def update_user(
        self,
        user_id: UUID,
        updates: dict[str, object],
    ) -> UserRecord | None:
        if not updates:
            return await self.get_user_by_id(user_id)

        assignments = ", ".join(f"{field} = %({field})s" for field in updates)
        query = f"""
            UPDATE app_users
            SET {assignments},
                updated_at = NOW()
            WHERE id = %(user_id)s
            RETURNING
                id,
                username,
                password_hash,
                is_active,
                is_admin,
                created_at,
                updated_at
        """

        params = {"user_id": user_id, **updates}

        async with self._pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(query, params)
                row = await cursor.fetchone()
            await connection.commit()

        if row is None:
            return None

        return UserRecord.model_validate(row)

    async def delete_user(self, user_id: UUID) -> bool:
        query = "DELETE FROM app_users WHERE id = %(user_id)s"

        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(query, {"user_id": user_id})
                deleted = cursor.rowcount > 0
            await connection.commit()

        return deleted

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

    async def list_api_keys_for_user(self, user_id: UUID) -> list[ApiKeyRecord]:
        query = """
            SELECT
                id,
                user_id,
                name,
                key_prefix,
                key_hash,
                is_active,
                last_used_at,
                created_at
            FROM api_keys
            WHERE user_id = %(user_id)s
            ORDER BY created_at DESC
        """

        async with self._pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(query, {"user_id": user_id})
                rows = await cursor.fetchall()

        return [ApiKeyRecord.model_validate(row) for row in rows]

    async def revoke_api_key(self, api_key_id: UUID, user_id: UUID | None = None) -> bool:
        query = """
            UPDATE api_keys
            SET is_active = FALSE
            WHERE id = %(api_key_id)s
        """
        params: dict[str, object] = {"api_key_id": api_key_id}
        if user_id is not None:
            query += " AND user_id = %(user_id)s"
            params["user_id"] = user_id

        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(query, params)
                updated = cursor.rowcount > 0
            await connection.commit()

        return updated

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
