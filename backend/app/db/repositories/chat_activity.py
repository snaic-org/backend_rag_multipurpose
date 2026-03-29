from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.models.schemas import ChatActivityRecord, ChatActivityWrite


class ChatActivityRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def ensure_table(self) -> None:
        queries = [
            """
            CREATE TABLE IF NOT EXISTS chat_activity_logs (
                id BIGSERIAL PRIMARY KEY,
                user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
                username TEXT NOT NULL,
                auth_type TEXT NOT NULL,
                request_path TEXT NOT NULL,
                client_ip TEXT,
                forwarded_for JSONB NOT NULL DEFAULT '[]'::jsonb,
                user_agent TEXT,
                session_id TEXT,
                request_message TEXT NOT NULL,
                response_answer TEXT,
                provider TEXT,
                model TEXT,
                embedding_profile TEXT,
                embedding_provider TEXT,
                embedding_model TEXT,
                used_fallback BOOLEAN NOT NULL DEFAULT FALSE,
                citations_count INTEGER NOT NULL DEFAULT 0,
                retrieved_chunks_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                error_message TEXT,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_chat_activity_logs_created_at ON chat_activity_logs (created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_chat_activity_logs_user_id ON chat_activity_logs (user_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_chat_activity_logs_client_ip ON chat_activity_logs (client_ip, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_chat_activity_logs_status ON chat_activity_logs (status, created_at DESC)",
        ]

        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                for query in queries:
                    await cursor.execute(query)
            await connection.commit()

    async def create(self, payload: ChatActivityWrite) -> ChatActivityRecord:
        query = """
            INSERT INTO chat_activity_logs (
                user_id,
                username,
                auth_type,
                request_path,
                client_ip,
                forwarded_for,
                user_agent,
                session_id,
                request_message,
                response_answer,
                provider,
                model,
                embedding_profile,
                embedding_provider,
                embedding_model,
                used_fallback,
                citations_count,
                retrieved_chunks_count,
                status,
                error_message,
                metadata
            )
            VALUES (
                %(user_id)s,
                %(username)s,
                %(auth_type)s,
                %(request_path)s,
                %(client_ip)s,
                %(forwarded_for)s,
                %(user_agent)s,
                %(session_id)s,
                %(request_message)s,
                %(response_answer)s,
                %(provider)s,
                %(model)s,
                %(embedding_profile)s,
                %(embedding_provider)s,
                %(embedding_model)s,
                %(used_fallback)s,
                %(citations_count)s,
                %(retrieved_chunks_count)s,
                %(status)s,
                %(error_message)s,
                %(metadata)s
            )
            RETURNING
                id,
                user_id,
                username,
                auth_type,
                request_path,
                client_ip,
                forwarded_for,
                user_agent,
                session_id,
                request_message,
                response_answer,
                provider,
                model,
                embedding_profile,
                embedding_provider,
                embedding_model,
                used_fallback,
                citations_count,
                retrieved_chunks_count,
                status,
                error_message,
                metadata,
                created_at
        """
        params = payload.model_dump()
        params["forwarded_for"] = Jsonb(payload.forwarded_for)
        params["metadata"] = Jsonb(payload.metadata)

        async with self._pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(query, params)
                row = await cursor.fetchone()
            await connection.commit()

        if row is None:
            raise RuntimeError("Failed to create chat activity log.")

        return ChatActivityRecord.model_validate(row)

    async def search(
        self,
        *,
        limit: int = 100,
        start_at=None,
        end_at=None,
        keyword: str | None = None,
    ) -> list[ChatActivityRecord]:
        conditions: list[str] = []
        params = {"limit": limit}

        if start_at is not None:
            conditions.append("created_at >= %(start_at)s")
            params["start_at"] = start_at

        if end_at is not None:
            conditions.append("created_at <= %(end_at)s")
            params["end_at"] = end_at

        if keyword:
            conditions.append(
                """(
                    request_message ILIKE %(keyword_pattern)s
                    OR COALESCE(response_answer, '') ILIKE %(keyword_pattern)s
                    OR username ILIKE %(keyword_pattern)s
                    OR COALESCE(client_ip, '') ILIKE %(keyword_pattern)s
                    OR COALESCE(provider, '') ILIKE %(keyword_pattern)s
                    OR COALESCE(model, '') ILIKE %(keyword_pattern)s
                    OR COALESCE(error_message, '') ILIKE %(keyword_pattern)s
                )"""
            )
            params["keyword_pattern"] = f"%{keyword}%"

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        query = f"""
            SELECT
                id,
                user_id,
                username,
                auth_type,
                request_path,
                client_ip,
                forwarded_for,
                user_agent,
                session_id,
                request_message,
                response_answer,
                provider,
                model,
                embedding_profile,
                embedding_provider,
                embedding_model,
                used_fallback,
                citations_count,
                retrieved_chunks_count,
                status,
                error_message,
                metadata,
                created_at
            FROM chat_activity_logs
            {where_clause}
            ORDER BY created_at DESC, id DESC
            LIMIT %(limit)s
        """

        async with self._pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(query, params)
                rows = await cursor.fetchall()

        return [ChatActivityRecord.model_validate(row) for row in rows]

    async def list_by_session_ids(self, session_ids: list[str]) -> list[ChatActivityRecord]:
        if not session_ids:
            return []

        query = """
            SELECT
                id,
                user_id,
                username,
                auth_type,
                request_path,
                client_ip,
                forwarded_for,
                user_agent,
                session_id,
                request_message,
                response_answer,
                provider,
                model,
                embedding_profile,
                embedding_provider,
                embedding_model,
                used_fallback,
                citations_count,
                retrieved_chunks_count,
                status,
                error_message,
                metadata,
                created_at
            FROM chat_activity_logs
            WHERE session_id = ANY(%(session_ids)s)
            ORDER BY created_at ASC, id ASC
        """

        async with self._pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(query, {"session_ids": session_ids})
                rows = await cursor.fetchall()

        return [ChatActivityRecord.model_validate(row) for row in rows]
