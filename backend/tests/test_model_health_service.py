import asyncio
from types import SimpleNamespace

import httpx

from app.models.schemas import ChatCompletionResult
from app.services.model_health_service import ModelHealthService

_UPSTREAM_404_BODY = '{"detail":"Function abc: Not found for account secret-account-id"}'


def _settings(**overrides) -> SimpleNamespace:
    values = {
        "health_model_probe_ttl_seconds": 300,
        "health_model_probe_timeout_seconds": 5.0,
        "rerank_enabled": False,
        "rerank_model": "nvidia/rerank",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeSelectionService:
    async def get_model_selection(self):
        return SimpleNamespace(
            generation_provider="nim",
            generation_model="nvidia/gen",
            embedding_provider="nim",
            embedding_model="nvidia/embed",
            embedding_profile="nim_embed",
        )


class FakeProvider:
    def __init__(self, behaviour: str) -> None:
        self.behaviour = behaviour
        self.calls = 0

    async def complete_chat(self, messages, model):
        self.calls += 1
        if self.behaviour == "retired":
            request = httpx.Request("POST", "https://integrate.api.nvidia.com/v1/chat/completions")
            response = httpx.Response(404, request=request, text=_UPSTREAM_404_BODY)
            raise httpx.HTTPStatusError("404 Not Found", request=request, response=response)
        if self.behaviour == "slow":
            await asyncio.sleep(1)
        if self.behaviour == "empty":
            return ChatCompletionResult(text="", provider="nim", model=model)
        return ChatCompletionResult(text="OK", provider="nim", model=model)


class FakeRegistry:
    def __init__(self, provider: FakeProvider) -> None:
        self._provider = provider

    def get(self, name: str) -> FakeProvider:
        return self._provider


class FakeEmbeddingService:
    def __init__(self) -> None:
        self.calls = []

    async def embed_texts(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(), [[0.0]]


class FakeRerankService:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def probe(self) -> None:
        self.calls += 1
        if self.fail:
            raise httpx.ConnectError("connection refused")


def _service(provider: FakeProvider, settings=None, rerank=None, embedding=None) -> ModelHealthService:
    return ModelHealthService(
        settings or _settings(),
        FakeRegistry(provider),
        FakeSelectionService(),
        embedding_service=embedding or FakeEmbeddingService(),
        rerank_service=rerank or FakeRerankService(),
    )


def test_model_health_ok_when_models_respond() -> None:
    embedding = FakeEmbeddingService()
    result = asyncio.run(_service(FakeProvider("ok"), embedding=embedding).check())

    assert result.status == "ok"
    assert result.generation.ok and result.generation.model == "nvidia/gen"
    assert result.embedding.ok
    assert result.rerank is None
    assert embedding.calls[0]["profile_name"] == "nim_embed"


def test_model_health_reports_retired_model_without_leaking_upstream_body() -> None:
    result = asyncio.run(_service(FakeProvider("retired")).check())

    assert result.status == "degraded"
    assert result.generation.ok is False
    assert "HTTP 404" in result.generation.detail
    assert "retired" in result.generation.detail
    assert "secret-account-id" not in result.generation.detail
    assert result.embedding.ok is True


def test_model_health_flags_empty_answer_and_timeout() -> None:
    empty = asyncio.run(_service(FakeProvider("empty")).check())
    assert empty.generation.ok is False
    assert "empty" in empty.generation.detail

    slow = asyncio.run(
        _service(FakeProvider("slow"), settings=_settings(health_model_probe_timeout_seconds=0.05)).check()
    )
    assert slow.generation.ok is False
    assert slow.generation.detail == "timed out"


def test_model_health_probes_rerank_only_when_enabled() -> None:
    rerank = FakeRerankService(fail=True)
    result = asyncio.run(
        _service(FakeProvider("ok"), settings=_settings(rerank_enabled=True), rerank=rerank).check()
    )

    assert rerank.calls == 1
    assert result.status == "degraded"
    assert result.rerank is not None and result.rerank.ok is False
    assert result.rerank.detail == "upstream unreachable: ConnectError"


def test_model_health_caches_results_within_ttl() -> None:
    provider = FakeProvider("ok")
    service = _service(provider)

    async def run_twice():
        first = await service.check()
        second = await service.check()
        return first, second

    first, second = asyncio.run(run_twice())

    assert provider.calls == 1
    assert first.cached is False
    assert second.cached is True


def test_model_health_serves_stale_result_while_refreshing_in_background() -> None:
    provider = FakeProvider("ok")
    service = _service(provider, settings=_settings(health_model_probe_ttl_seconds=0))

    async def run():
        await service.check()
        provider.behaviour = "retired"
        started = asyncio.get_running_loop().time()
        stale = await service.check()
        elapsed = asyncio.get_running_loop().time() - started
        await service._refresh_task
        refreshed = await service.check()
        return stale, elapsed, refreshed

    stale, elapsed, refreshed = asyncio.run(run())

    assert stale.cached is True
    assert stale.status == "ok"
    assert elapsed < 0.5
    assert refreshed.status == "degraded"
    assert provider.calls >= 2


def test_model_health_runs_one_background_refresh_at_a_time() -> None:
    provider = FakeProvider("ok")
    service = _service(provider, settings=_settings(health_model_probe_ttl_seconds=0))

    async def run():
        await service.check()
        provider.behaviour = "slow"
        await asyncio.gather(*(service.check() for _ in range(5)))
        await service._refresh_task

    asyncio.run(run())

    assert provider.calls == 2


def test_model_health_recheck_after_upstream_failure_reprobes_despite_long_ttl() -> None:
    provider = FakeProvider("ok")
    service = _service(provider, settings=_settings(health_model_probe_ttl_seconds=86400))

    async def run():
        await service.check()
        provider.behaviour = "retired"
        service._cached_at -= 120  # last probe was two minutes ago
        service.request_recheck()
        await service.check()  # serves the stale result, starts a re-probe
        await service._refresh_task
        return await service.check()

    result = asyncio.run(run())

    assert provider.calls == 2
    assert result.status == "degraded"


def test_model_health_recheck_is_rate_limited() -> None:
    provider = FakeProvider("ok")
    service = _service(provider, settings=_settings(health_model_probe_ttl_seconds=86400))

    async def run():
        await service.check()
        service.request_recheck()  # the last probe was just now
        await service.check()

    asyncio.run(run())

    assert provider.calls == 1
    assert service._refresh_task is None


def test_model_health_rechecks_failed_result_sooner_than_ttl() -> None:
    provider = FakeProvider("retired")
    service = _service(provider, settings=_settings(health_model_probe_ttl_seconds=86400))

    async def run():
        first = await service.check()
        provider.behaviour = "ok"
        service._cached_at -= 301  # older than the unhealthy re-check interval
        await service.check()
        await service._refresh_task
        return first, await service.check()

    first, recovered = asyncio.run(run())

    assert first.status == "degraded"
    assert recovered.status == "ok"


def test_model_health_aclose_cancels_background_refresh() -> None:
    provider = FakeProvider("ok")
    service = _service(provider, settings=_settings(health_model_probe_ttl_seconds=0))

    async def run():
        await service.check()
        provider.behaviour = "slow"
        await service.check()  # starts a slow background refresh
        await service.aclose()
        return service._refresh_task

    task = asyncio.run(run())

    assert task.cancelled()
