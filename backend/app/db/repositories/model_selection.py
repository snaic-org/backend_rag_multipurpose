from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.models.schemas import ModelSelectionRecord


class ModelSelectionRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def ensure_model_selection_table(
        self,
        default_generation_profile: str,
        default_embedding_profile: str,
    ) -> None:
        queries: list[tuple[str, dict[str, object] | None]] = [
            (
                """
            CREATE TABLE IF NOT EXISTS model_selection_settings (
                id SMALLINT PRIMARY KEY CHECK (id = 1),
                generation_profile TEXT NOT NULL,
                embedding_profile TEXT NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
                None,
            ),
            (
                """
            INSERT INTO model_selection_settings (
                id,
                generation_profile,
                embedding_profile
            )
            VALUES (
                1,
                %(generation_profile)s,
                %(embedding_profile)s
            )
            ON CONFLICT (id) DO NOTHING
            """,
                {
                    "generation_profile": default_generation_profile,
                    "embedding_profile": default_embedding_profile,
                },
            ),
        ]

        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                for query, params in queries:
                    if params is None:
                        await cursor.execute(query)
                    else:
                        await cursor.execute(query, params)
            await connection.commit()

    async def ensure_default_model_selection(
        self,
        default_generation_profile: str,
        default_embedding_profile: str,
    ) -> None:
        current = await self.get_model_selection()
        if current is None:
            await self.update_model_selection(default_generation_profile, default_embedding_profile)

    async def get_model_selection(self) -> ModelSelectionRecord | None:
        query = """
            SELECT
                id,
                generation_profile,
                embedding_profile,
                updated_at
            FROM model_selection_settings
            WHERE id = 1
        """

        async with self._pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(query)
                row = await cursor.fetchone()

        if row is None:
            return None

        return ModelSelectionRecord.model_validate(row)

    async def update_model_selection(
        self,
        generation_profile: str,
        embedding_profile: str,
    ) -> ModelSelectionRecord:
        query = """
            INSERT INTO model_selection_settings (
                id,
                generation_profile,
                embedding_profile
            )
            VALUES (
                1,
                %(generation_profile)s,
                %(embedding_profile)s
            )
            ON CONFLICT (id) DO UPDATE
                SET generation_profile = EXCLUDED.generation_profile,
                    embedding_profile = EXCLUDED.embedding_profile,
                    updated_at = NOW()
            RETURNING
                id,
                generation_profile,
                embedding_profile,
                updated_at
        """

        async with self._pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(
                    query,
                    {
                        "generation_profile": generation_profile,
                        "embedding_profile": embedding_profile,
                    },
                )
                row = await cursor.fetchone()
            await connection.commit()

        if row is None:
            raise RuntimeError("Failed to update model selection.")

        return ModelSelectionRecord.model_validate(row)
