from types import SimpleNamespace

from app.models.schemas import ChatMessage
from app.providers.nim_provider import NimProvider


def test_nim_provider_preserves_original_system_prompt_order() -> None:
    settings = SimpleNamespace(
        nim_base_url="https://integrate.api.nvidia.com/v1",
        nim_api_key="test-key",
        chat_temperature=0.0,
        chat_show_thinking_block=False,
        chat_thinking_enabled=False,
    )
    provider = NimProvider(settings)

    messages = [
        ChatMessage(role="system", content="real system prompt"),
        ChatMessage(role="user", content="hello"),
    ]

    built = provider._build_messages(messages, thinking_enabled=False)

    assert built == [
        {"role": "system", "content": "real system prompt"},
        {"role": "user", "content": "hello"},
    ]


def test_nim_provider_payload_uses_chat_template_kwargs_without_injected_hint() -> None:
    settings = SimpleNamespace(
        nim_base_url="https://integrate.api.nvidia.com/v1",
        nim_api_key="test-key",
        chat_temperature=0.1,
        chat_show_thinking_block=False,
        chat_thinking_enabled=True,
    )
    provider = NimProvider(settings)

    payload = provider._build_payload(
        messages=[ChatMessage(role="system", content="follow these rules")],
        model="nvidia/nemotron-3.5-super-vl-120b-a12b",
        thinking_enabled=True,
        stream=False,
    )

    assert payload["messages"] == [{"role": "system", "content": "follow these rules"}]
    assert payload["chat_template_kwargs"] == {"enable_thinking": True}


def test_nim_healthcheck_requires_api_key_for_hosted_api() -> None:
    import asyncio

    from app.core.config import Settings

    hosted = asyncio.run(NimProvider(Settings(nim_api_key=None)).healthcheck())
    assert hosted.ok is False
    assert hosted.detail == "missing NIM_API_KEY"

    self_hosted = asyncio.run(
        NimProvider(Settings(nim_api_key=None, nim_base_url="http://nim.internal:8000/v1")).healthcheck()
    )
    assert self_hosted.ok is True
