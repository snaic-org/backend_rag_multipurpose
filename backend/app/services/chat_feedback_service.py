from datetime import datetime

from psycopg_pool import AsyncConnectionPool

from app.db.repositories.chat_activity import ChatActivityRepository
from app.db.repositories.chat_feedback import ChatFeedbackRepository
from app.models.schemas import (
    AuthenticatedUser,
    ChatActivityRecord,
    ChatFeedbackRequest,
    ChatFeedbackResponse,
)


class ChatFeedbackService:
    def __init__(self, postgres_pool: AsyncConnectionPool) -> None:
        self._repository = ChatFeedbackRepository(postgres_pool)
        self._activity_repository = ChatActivityRepository(postgres_pool)

    async def ensure_table(self) -> None:
        await self._repository.ensure_table()

    async def submit_feedback(
        self,
        payload: ChatFeedbackRequest,
        current_user: AuthenticatedUser,
    ) -> ChatFeedbackResponse:
        record = await self._repository.create(
            user_id=current_user.id,
            username=current_user.username,
            session_id=payload.session_id,
            rating=payload.rating,
            comments=payload.comments,
        )
        activities = await self._activity_repository.list_by_session_ids([record.session_id])
        return self._to_feedback_response(record, activities)

    async def list_feedback(
        self,
        *,
        limit: int = 100,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> list[ChatFeedbackResponse]:
        records = await self._repository.list_feedback(
            limit=limit,
            start_at=start_at,
            end_at=end_at,
        )
        activities = await self._activity_repository.list_by_session_ids(
            [record.session_id for record in records]
        )
        grouped: dict[str, list[ChatActivityRecord]] = {}
        for activity in activities:
            if activity.session_id is None:
                continue
            grouped.setdefault(activity.session_id, []).append(activity)
        return [
            self._to_feedback_response(record, grouped.get(record.session_id, []))
            for record in records
        ]

    def _to_feedback_response(
        self,
        record,
        activities: list[ChatActivityRecord],
    ) -> ChatFeedbackResponse:
        return ChatFeedbackResponse(
            id=record.id,
            session_id=record.session_id,
            rating=record.rating,
            full_chat_text=self._build_full_chat_text(activities),
            comments=record.comments,
            date=record.created_at,
            created_at=record.created_at,
        )

    def _build_full_chat_text(self, activities: list[ChatActivityRecord]) -> str:
        parts: list[str] = []
        for activity in activities:
            if activity.request_message:
                parts.append(f"User: {activity.request_message}")
            if activity.response_answer:
                parts.append(f"Assistant: {activity.response_answer}")
        return "\n\n".join(parts)
