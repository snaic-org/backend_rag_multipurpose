from uuid import UUID, uuid4

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.models.schemas import UserRecord


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

