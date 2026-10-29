import asyncio
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable

import httpx

from app.core.config import Settings
from app.core.logging import get_logger
from app.models.schemas import ChatMessage, ModelHealthResponse, ModelProbeResult, ModelSelectionResponse
from app.providers.registry import ProviderRegistry
from app.services.embeddings import EmbeddingService
from app.services.model_selection_service import ModelSelectionService
from app.services.rerank import RerankService

logger = get_logger(__name__)

# A burst of failing chat requests may trigger at most one re-probe per interval.
_MIN_RECHECK_INTERVAL_SECONDS = 60
# Failed results are re-probed this often (or per the TTL, if shorter) so a
# recovery is noticed without waiting out a long TTL.
_UNHEALTHY_RECHECK_SECONDS = 300

_PROBE_MESSAGES = [
    ChatMessage(role="system", content="Reply with the single word OK."),
    ChatMessage(role="user", content="Health check."),
]


class ModelHealthService:
    """Calls the active generation, embedding, and rerank models for real.

    Results are cached for HEALTH_MODEL_PROBE_TTL_SECONDS so the public endpoint
    cannot be used to run up upstream API usage. Once a result exists, an expired
    one is returned immediately while a single background probe refreshes it:
    uptime checkers such as Route 53 give up after 2 seconds, and a real model
    call can take longer than that.
    """

    def __init__(
        self,
        settings: Settings,
        provider_registry: ProviderRegistry,
        model_selection_service: ModelSelectionService,
        embedding_service: EmbeddingService | None = None,
        rerank_service: RerankService | None = None,
    ) -> None:
        self._settings = settings
        self._providers = provider_registry
        self._model_selection_service = model_selection_service
        self._embedding_service = embedding_service or EmbeddingService(settings)
        self._rerank_service = rerank_service or RerankService(settings)
        self._lock = asyncio.Lock()
        self._cached: ModelHealthResponse | None = None
        self._cached_at = 0.0
        self._refresh_task: asyncio.Task | None = None

    async def check(self) -> ModelHealthResponse:
        if self._cached is None:
            async with self._lock:
                if self._cached is None:
                    await self._refresh()
                    return self._cached

        ttl = self._settings.health_model_probe_ttl_seconds
        if self._cached.status != "ok":
            ttl = min(ttl, _UNHEALTHY_RECHECK_SECONDS)
        if time.monotonic() - self._cached_at >= ttl:
            if self._refresh_task is None or self._refresh_task.done():
                self._refresh_task = asyncio.create_task(self._refresh_in_background())

        return self._cached.model_copy(update={"cached": True})

    def request_recheck(self) -> None:
        """Expire the cached result after a real request hit an upstream error.

        With a long TTL (e.g. one probe a day) this keeps detection fast: the next
        check re-probes instead of serving a result that predates the failure.
        """
        if self._cached is None:
            return
        if time.monotonic() - self._cached_at < _MIN_RECHECK_INTERVAL_SECONDS:
            return
        self._cached_at = float("-inf")

    async def _refresh(self) -> None:
        self._cached = await self._run_probes()
        self._cached_at = time.monotonic()

    async def _refresh_in_background(self) -> None:
        try:
            async with self._lock:
                await self._refresh()
        except Exception as exc:  # pragma: no cover - probes already catch their own errors
            logger.warning("model_health_refresh_failed error=%r", exc)

    async def _run_probes(self) -> ModelHealthResponse:
        selection = await self._model_selection_service.get_model_selection()

        probes = [
            self._probe(
                "generation",
                selection.generation_provider,
                selection.generation_model,
                lambda: self._probe_generation(selection),
            ),
            self._probe(
                "embedding",
                selection.embedding_provider,
                selection.embedding_model,
                lambda: self._probe_embedding(selection),
            ),
        ]
        if self._settings.rerank_enabled:
            probes.append(
                self._probe("rerank", "nim", self._settings.rerank_model, self._rerank_service.probe)
            )

        generation, embedding, *rest = await asyncio.gather(*probes)
        rerank = rest[0] if rest else None
        checks = [generation, embedding, *([rerank] if rerank else [])]

        return ModelHealthResponse(
            status="ok" if all(check.ok for check in checks) else "degraded",
            generation=generation,
            embedding=embedding,
            rerank=rerank,
            checked_at=datetime.now(timezone.utc),
        )

    async def _probe_generation(self, selection: ModelSelectionResponse) -> None:
        completion = await self._providers.get(selection.generation_provider).complete_chat(
            messages=_PROBE_MESSAGES,
            model=selection.generation_model,
        )
        if not (completion.text or "").strip():
            raise ValueError("model returned an empty response")

    async def _probe_embedding(self, selection: ModelSelectionResponse) -> None:
        # embed_texts also verifies the vector dimension matches the profile.
        await self._embedding_service.embed_texts(
            texts=["health check"],
            profile_name=selection.embedding_profile,
            input_type="query",
        )

    async def _probe(
        self,
        check: str,
        provider: str,
        model: str,
        call: Callable[[], Awaitable[None]],
    ) -> ModelProbeResult:
        started = time.monotonic()
        try:
            await asyncio.wait_for(call(), timeout=self._settings.health_model_probe_timeout_seconds)
        except Exception as exc:
            body = exc.response.text[:500] if isinstance(exc, httpx.HTTPStatusError) else ""
            logger.warning(
                "model_health_probe_failed check=%s provider=%s model=%s error=%r body=%s",
                check,
                provider,
                model,
                exc,
                body,
            )
            return ModelProbeResult(
                ok=False,
                detail=_describe_error(exc),
                provider=provider,
                model=model,
                latency_ms=_elapsed_ms(started),
            )

        return ModelProbeResult(
            ok=True,
            detail="responding",
            provider=provider,
            model=model,
            latency_ms=_elapsed_ms(started),
        )


def _describe_error(exc: Exception) -> str:
    # The endpoint is public, so report the failure class without echoing
    # upstream response bodies (they can include account identifiers).
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        detail = f"upstream HTTP {status_code} {exc.response.reason_phrase}".strip()
        if status_code in {404, 410}:
            detail += " - model retired or not available for this API key"
        elif status_code in {401, 403}:
            detail += " - check the API key"
        return detail
    if isinstance(exc, (asyncio.TimeoutError, httpx.TimeoutException)):
        return "timed out"
    if isinstance(exc, httpx.HTTPError):
        return f"upstream unreachable: {type(exc).__name__}"
    return str(exc) or type(exc).__name__


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
