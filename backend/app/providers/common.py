"""Helpers shared by the chat provider adapters and the chat service."""

import re

import httpx

from app.core.config import Settings

_THINKING_BLOCK_RE = re.compile(r"<(?P<tag>think|thinking)>.*?</(?P=tag)>", re.IGNORECASE | re.DOTALL)
_RETRYABLE_THINKING_STATUSES = {400, 404, 422}


def is_thinking_enabled(settings: Settings) -> bool:
    return bool(getattr(settings, "chat_thinking_enabled", False))


def bearer_headers(api_key: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


async def raise_for_status(response: httpx.Response) -> None:
    """raise_for_status that first reads a streamed error body.

    Without the read, inspecting ``exc.response.text`` on a streamed response
    raises ``ResponseNotRead`` instead of returning the provider's error message.
    """
    if response.is_error:
        await response.aread()
    response.raise_for_status()


def should_retry_without_thinking(
    settings: Settings,
    exc: httpx.HTTPStatusError,
    error_tokens: tuple[str, ...],
) -> bool:
    """True when a request failed because the model rejected reasoning/thinking mode."""
    if not is_thinking_enabled(settings):
        return False
    if getattr(exc.response, "status_code", None) not in _RETRYABLE_THINKING_STATUSES:
        return False
    detail = " ".join(
        part
        for part in [
            str(exc),
            getattr(exc.response, "text", ""),
            getattr(exc.response, "reason_phrase", ""),
        ]
        if part
    ).lower()
    return any(token in detail for token in error_tokens)


def contains_thinking_block(text: str | None) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return "<think>" in lowered or "<thinking>" in lowered


def strip_thinking_blocks(text: str | None) -> str:
    """Remove complete <think>/<thinking> blocks from a finished answer."""
    if not text:
        return ""
    stripped = _THINKING_BLOCK_RE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", stripped).strip()


def extract_inline_thinking(text: str) -> str | None:
    """The contents of the first <think>/<thinking> block in text, if any."""
    lowered = text.lower()
    start = lowered.find("<think>")
    start_alt = lowered.find("<thinking>")
    if start == -1 and start_alt == -1:
        return None

    if start_alt == -1 or (start != -1 and start < start_alt):
        open_tag = "<think>"
        close_tag = "</think>"
        start_index = start
    else:
        open_tag = "<thinking>"
        close_tag = "</thinking>"
        start_index = start_alt

    end = lowered.find(close_tag, start_index + len(open_tag))
    if end == -1:
        return None

    return text[start_index + len(open_tag) : end].strip() or None


def strip_thinking_delta(text: str, in_thinking: bool) -> tuple[str, bool]:
    """Remove thinking blocks from one streamed delta.

    Blocks can span deltas, so the caller passes back the returned
    ``in_thinking`` state with the next delta.
    """
    output: list[str] = []
    remaining = text

    while remaining:
        if in_thinking:
            end = remaining.find("</think>")
            end_alt = remaining.find("</thinking>")
            if end == -1 and end_alt == -1:
                return "".join(output), True
            if end_alt == -1 or (end != -1 and end < end_alt):
                remaining = remaining[end + len("</think>") :]
            else:
                remaining = remaining[end_alt + len("</thinking>") :]
            in_thinking = False
            continue

        start = remaining.find("<think>")
        start_alt = remaining.find("<thinking>")
        if start == -1 and start_alt == -1:
            output.append(remaining)
            return "".join(output), False

        if start_alt == -1 or (start != -1 and start < start_alt):
            output.append(remaining[:start])
            remaining = remaining[start + len("<think>") :]
        else:
            output.append(remaining[:start_alt])
            remaining = remaining[start_alt + len("<thinking>") :]
        in_thinking = True

    return "".join(output), in_thinking
