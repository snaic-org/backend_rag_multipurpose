from collections import Counter
from datetime import datetime

from psycopg_pool import AsyncConnectionPool

from app.db.repositories.chat_activity import ChatActivityRepository
from app.models.schemas import (
    ChatActivityOverview,
    ChatActivityQueryResponse,
    ChatActivityResponse,
    ChatActivityWrite,
)


class ChatActivityService:
    def __init__(self, postgres_pool: AsyncConnectionPool) -> None:
        self._repository = ChatActivityRepository(postgres_pool)

    async def record(self, payload: ChatActivityWrite) -> ChatActivityResponse:
        record = await self._repository.create(payload)
        return ChatActivityResponse.model_validate(record)

    async def search(
        self,
        *,
        limit: int = 100,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        keyword: str | None = None,
    ) -> ChatActivityQueryResponse:
        records = await self._repository.search(
            limit=limit,
            start_at=start_at,
            end_at=end_at,
            keyword=keyword.strip() if keyword else None,
        )
        activities = [ChatActivityResponse.model_validate(record) for record in records]
        return ChatActivityQueryResponse(
            overview=self._build_overview(activities),
            activities=activities,
        )

    def _build_overview(self, activities: list[ChatActivityResponse]) -> ChatActivityOverview:
        provider_counts = Counter(activity.provider for activity in activities if activity.provider)
        model_counts = Counter(activity.model for activity in activities if activity.model)
        created_at_values = [activity.created_at for activity in activities]

        return ChatActivityOverview(
            total_activities=len(activities),
            completed_activities=sum(1 for activity in activities if activity.status == "completed"),
            failed_activities=sum(1 for activity in activities if activity.status == "failed"),
            fallback_activities=sum(1 for activity in activities if activity.used_fallback),
            unique_users=len({activity.user_id for activity in activities}),
            unique_client_ips=len({activity.client_ip for activity in activities if activity.client_ip}),
            first_activity_at=min(created_at_values) if created_at_values else None,
            last_activity_at=max(created_at_values) if created_at_values else None,
            top_providers=[name for name, _ in provider_counts.most_common(5)],
            top_models=[name for name, _ in model_counts.most_common(5)],
        )
