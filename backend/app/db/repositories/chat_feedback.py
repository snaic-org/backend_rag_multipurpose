from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.models.schemas import ChatFeedbackRecord


class ChatFeedbackRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def ensure_table(self) -> None:
        queries = [
            """
            CREATE TABLE IF NOT EXISTS chat_feedback (
                id BIGSERIAL PRIMARY KEY,
                user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
                username TEXT NOT NULL,
                session_id TEXT NOT NULL,
                rating SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
                comments TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_chat_feedback_session_id ON chat_feedback (session_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_chat_feedback_user_id ON chat_feedback (user_id, created_at DESC)",
        ]

        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                for query in queries:
                    await cursor.execute(query)
            await connection.commit()

    async def create(
        self,
        *,
        user_id,
        username: str,
        session_id: str,
        rating: int,
        comments: str | None,
    ) -> ChatFeedbackRecord:
        query = """
            INSERT INTO chat_feedback (
                user_id,
                username,
                session_id,
                rating,
                comments
            )
            VALUES (
                %(user_id)s,
                %(username)s,
                %(session_id)s,
                %(rating)s,
                %(comments)s
            )
            RETURNING
                id,
                user_id,
                username,
                session_id,
                rating,
                comments,
                created_at
        """
        params = {
            "user_id": user_id,
            "username": username,
            "session_id": session_id,
            "rating": rating,
            "comments": comments,
        }

        async with self._pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(query, params)
                row = await cursor.fetchone()
            await connection.commit()

        if row is None:
            raise RuntimeError("Failed to save chat feedback.")

        return ChatFeedbackRecord.model_validate(row)

    async def list_feedback(
        self,
        *,
        limit: int = 100,
        start_at=None,
        end_at=None,
    ) -> list[ChatFeedbackRecord]:
        conditions: list[str] = []
        params = {"limit": limit}

        if start_at is not None:
            conditions.append("created_at >= %(start_at)s")
            params["start_at"] = start_at

        if end_at is not None:
            conditions.append("created_at <= %(end_at)s")
            params["end_at"] = end_at

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        query = f"""
            SELECT
                id,
                user_id,
                username,
                session_id,
                rating,
                comments,
                created_at
            FROM chat_feedback
            {where_clause}
            ORDER BY created_at DESC, id DESC
            LIMIT %(limit)s
        """

        async with self._pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(query, params)
                rows = await cursor.fetchall()

        return [ChatFeedbackRecord.model_validate(row) for row in rows]
