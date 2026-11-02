from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.models.schemas import SystemPromptRecord


class SystemPromptRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def get_system_prompt(self) -> SystemPromptRecord | None:
        query = """
            SELECT
                id,
                system_prompt,
                updated_at,
                is_custom
            FROM system_prompt_settings
            WHERE id = 1
        """

        async with self._pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(query)
                row = await cursor.fetchone()

        if row is None:
            return None

        return SystemPromptRecord.model_validate(row)

    async def update_system_prompt(self, system_prompt: str, *, is_custom: bool = False) -> SystemPromptRecord:
        query = """
            INSERT INTO system_prompt_settings (
                id,
                system_prompt,
                is_custom
            )
            VALUES (
                1,
                %(system_prompt)s,
                %(is_custom)s
            )
            ON CONFLICT (id) DO UPDATE
                SET system_prompt = EXCLUDED.system_prompt,
                    is_custom = EXCLUDED.is_custom,
                    updated_at = NOW()
            RETURNING
                id,
                system_prompt,
                updated_at,
                is_custom
        """

        async with self._pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(query, {"system_prompt": system_prompt, "is_custom": is_custom})
                row = await cursor.fetchone()
            await connection.commit()

        if row is None:
            raise RuntimeError("Failed to update system prompt.")

        return SystemPromptRecord.model_validate(row)
