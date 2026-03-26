from psycopg_pool import AsyncConnectionPool

from app.core.config import Settings
from app.db.repositories.model_selection import ModelSelectionRepository
from app.models.schemas import (
    ModelCatalogResponse,
    ModelSelectionResponse,
)


class ModelSelectionService:
    def __init__(self, settings: Settings, postgres_pool: AsyncConnectionPool) -> None:
        self._settings = settings
        self._repository = ModelSelectionRepository(postgres_pool)

    async def ensure_default_model_selection(self) -> None:
        generation_profile = self._resolve_default_generation_profile_name()
        embedding_profile = self._resolve_default_embedding_profile_name()
        await self._repository.ensure_model_selection_table(
            generation_profile,
            embedding_profile,
        )
        await self._repository.ensure_default_model_selection(
            generation_profile,
            embedding_profile,
        )

    async def get_model_selection(self) -> ModelSelectionResponse:
        record = await self._repository.get_model_selection()
        if record is None:
            record = await self._repository.update_model_selection(
                self._resolve_default_generation_profile_name(),
                self._resolve_default_embedding_profile_name(),
            )
        return self._to_response(record.generation_profile, record.embedding_profile, record.updated_at)

    async def update_model_selection(
        self,
        generation_profile: str,
        embedding_profile: str,
    ) -> ModelSelectionResponse:
        await self._validate_selection(generation_profile, embedding_profile)
        record = await self._repository.update_model_selection(generation_profile, embedding_profile)
        return self._to_response(record.generation_profile, record.embedding_profile, record.updated_at)

    async def get_catalog(self) -> ModelCatalogResponse:
        return ModelCatalogResponse(
            generation_profiles=[
                self._to_generation_catalog_item(name, spec)
                for name, spec in self._settings.generation_profiles.items()
            ],
            embedding_profiles=[
                self._to_embedding_catalog_item(name, spec)
                for name, spec in self._settings.embedding_profiles.items()
            ],
        )

    async def get_generation_profile_name(self) -> str:
        selection = await self.get_model_selection()
        return selection.generation_profile

    async def get_embedding_profile_name(self) -> str:
        selection = await self.get_model_selection()
        return selection.embedding_profile

    async def _validate_selection(self, generation_profile: str, embedding_profile: str) -> None:
        generation_names = set(self._settings.generation_profiles)
        embedding_names = set(self._settings.embedding_profiles)

        if generation_profile not in generation_names:
            raise ValueError(f"Unknown generation profile '{generation_profile}'")
        if embedding_profile not in embedding_names:
            raise ValueError(f"Unknown embedding profile '{embedding_profile}'")

    def _to_response(
        self,
        generation_profile: str,
        embedding_profile: str,
        updated_at,
    ) -> ModelSelectionResponse:
        generation_spec = self._settings.generation_profiles.get(generation_profile)
        if generation_spec is None:
            raise ValueError(f"Unknown generation profile '{generation_profile}'")

        embedding_spec = self._settings.embedding_profiles.get(embedding_profile)
        if embedding_spec is None:
            raise ValueError(f"Unknown embedding profile '{embedding_profile}'")

        return ModelSelectionResponse(
            generation_profile=generation_profile,
            generation_provider=generation_spec.provider,
            generation_model=generation_spec.model,
            embedding_profile=embedding_profile,
            embedding_provider=embedding_spec.provider,
            embedding_model=embedding_spec.model,
            embedding_dimension=embedding_spec.dimension,
            updated_at=updated_at,
        )

    def _to_generation_catalog_item(self, profile_name: str, spec):
        from app.models.schemas import ModelCatalogGenerationProfile

        return ModelCatalogGenerationProfile(
            profile_name=profile_name,
            provider=spec.provider,
            model=spec.model,
        )

    def _to_embedding_catalog_item(self, profile_name: str, spec):
        from app.models.schemas import ModelCatalogEmbeddingProfile

        return ModelCatalogEmbeddingProfile(
            profile_name=profile_name,
            provider=spec.provider,
            model=spec.model,
            dimension=spec.dimension,
        )

    def _resolve_default_generation_profile_name(self) -> str:
        return self._resolve_profile_name(
            self._settings.generation_profiles,
            self._settings.default_generation_provider,
            self._settings.default_generation_model,
        )

    def _resolve_default_embedding_profile_name(self) -> str:
        return self._resolve_embedding_profile_name(
            self._settings.embedding_profiles,
            self._settings.default_embedding_provider,
            self._settings.default_embedding_model,
            self._settings.default_embedding_dimension,
        )

    def _resolve_profile_name(self, catalog, provider, model) -> str:
        matches = [
            name
            for name, spec in catalog.items()
            if spec.provider == provider and spec.model == model
        ]
        if not matches:
            raise ValueError(f"Unknown default generation provider/model pair '{provider}/{model}'")
        if len(matches) > 1:
            raise ValueError(
                f"Multiple generation profiles match the default provider/model pair '{provider}/{model}'"
            )
        return matches[0]

    def _resolve_embedding_profile_name(self, catalog, provider, model, dimension) -> str:
        matches = [
            name
            for name, spec in catalog.items()
            if spec.provider == provider and spec.model == model and spec.dimension == dimension
        ]
        if not matches:
            raise ValueError(
                f"Unknown default embedding provider/model/dimension triple '{provider}/{model}/{dimension}'"
            )
        if len(matches) > 1:
            raise ValueError(
                "Multiple embedding profiles match the default provider/model/dimension triple"
            )
        return matches[0]
