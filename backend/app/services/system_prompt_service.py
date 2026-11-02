from psycopg_pool import AsyncConnectionPool

from app.db.repositories.system_prompt import SystemPromptRepository
from app.models.schemas import SystemPromptRecord, SystemPromptResponse
from app.services.prompt_builder import DEFAULT_SYSTEM_PROMPT


class SystemPromptService:
    """The chat system prompt: the built-in default, or one an admin saved.

    A prompt saved through PUT /admin/system-prompt (the console's prompt
    editor) is marked custom and kept across restarts. Otherwise startup rolls
    out the current built-in DEFAULT_SYSTEM_PROMPT, so prompt improvements in
    code reach deployed servers.
    """

    def __init__(self, postgres_pool: AsyncConnectionPool) -> None:
        self._repository = SystemPromptRepository(postgres_pool)

    async def ensure_default_system_prompt(self) -> None:
        record = await self._repository.get_system_prompt()
        if record is None or (not record.is_custom and record.system_prompt != DEFAULT_SYSTEM_PROMPT):
            await self._repository.update_system_prompt(DEFAULT_SYSTEM_PROMPT, is_custom=False)

    async def get_system_prompt(self) -> SystemPromptResponse:
        record = await self._repository.get_system_prompt()
        if record is None:
            record = await self._repository.update_system_prompt(DEFAULT_SYSTEM_PROMPT, is_custom=False)
        return _response(record)

    async def update_system_prompt(self, system_prompt: str) -> SystemPromptResponse:
        is_custom = system_prompt != DEFAULT_SYSTEM_PROMPT
        return _response(await self._repository.update_system_prompt(system_prompt, is_custom=is_custom))

    async def reset_system_prompt(self) -> SystemPromptResponse:
        return _response(await self._repository.update_system_prompt(DEFAULT_SYSTEM_PROMPT, is_custom=False))


def _response(record: SystemPromptRecord) -> SystemPromptResponse:
    return SystemPromptResponse(
        system_prompt=record.system_prompt,
        updated_at=record.updated_at,
        is_custom=record.is_custom,
    )
