from psycopg_pool import AsyncConnectionPool

from app.db.repositories.system_prompt import SystemPromptRepository
from app.models.schemas import SystemPromptResponse
from app.services.prompt_builder import DEFAULT_SYSTEM_PROMPT, is_managed_system_prompt


class SystemPromptService:
    def __init__(self, postgres_pool: AsyncConnectionPool) -> None:
        self._repository = SystemPromptRepository(postgres_pool)

    async def ensure_default_system_prompt(self) -> None:
        await self._repository.ensure_system_prompt_table(DEFAULT_SYSTEM_PROMPT)
        record = await self._repository.get_system_prompt()
        if record is None:
            await self._repository.update_system_prompt(DEFAULT_SYSTEM_PROMPT)
            return

        if record.system_prompt == DEFAULT_SYSTEM_PROMPT:
            return

        if is_managed_system_prompt(record.system_prompt):
            await self._repository.update_system_prompt(DEFAULT_SYSTEM_PROMPT)

    async def get_system_prompt(self) -> SystemPromptResponse:
        record = await self._repository.get_system_prompt()
        if record is None:
            record = await self._repository.update_system_prompt(DEFAULT_SYSTEM_PROMPT)
        return SystemPromptResponse(
            system_prompt=record.system_prompt,
            updated_at=record.updated_at,
        )

    async def update_system_prompt(self, system_prompt: str) -> SystemPromptResponse:
        record = await self._repository.update_system_prompt(system_prompt)
        return SystemPromptResponse(
            system_prompt=record.system_prompt,
            updated_at=record.updated_at,
        )
