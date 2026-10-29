from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher

from redis.asyncio import Redis

from app.core.config import (
    CHAT_MAX_RESPONSE_CHARS,
    CHAT_MAX_RESPONSE_TOKENS,
    CHAT_MAX_TOP_K,
    CHAT_MIN_TOP_K,
    CHAT_REPEATED_PROMPT_LOOKBACK,
    Settings,
)
from app.core.rate_limit import RateLimiter
from app.models.schemas import ChatMessage
from app.services.assistant_copy import SOURCE_INTROSPECTION_REFUSAL_TEXT


_MIN_REPEAT_CHECK_CHARS = 24


class GuardrailService:
    def __init__(self, settings: Settings, redis_client: Redis) -> None:
        self._settings = settings
        self._redis = redis_client
        self._burst_limiter = RateLimiter(
            redis_client=redis_client,
            limit=settings.chat_rate_limit_requests,
            window_seconds=settings.chat_rate_limit_window_seconds,
        )
        self._blocked_patterns = [
            re.compile(r"\bignore\s+previous\s+instructions\b", re.IGNORECASE),
            re.compile(r"\bdump\s+all\s+data\b", re.IGNORECASE),
            re.compile(r"\bshow\s+full\s+document\b", re.IGNORECASE),
            re.compile(r"\bexport\s+everything\b", re.IGNORECASE),
            re.compile(r"\bprint\s+full\s+source\b", re.IGNORECASE),
            re.compile(r"\breturn\s+exact\s+text\b", re.IGNORECASE),
        ]
        self._source_introspection_patterns = [
            re.compile(r"\b(which|what)\s+document\s+did\s+you\s+use\b", re.IGNORECASE),
            re.compile(r"\b(which|what)\s+sources?\s+did\s+you\s+use\b", re.IGNORECASE),
            re.compile(r"\bwhich\s+document\s+you\s+used\b", re.IGNORECASE),
            re.compile(r"\bwhat\s+document\s+you\s+used\b", re.IGNORECASE),
            re.compile(r"\bwhich\s+source(?:s)?\b.*\b(?:used|use|based on)\b", re.IGNORECASE),
            re.compile(r"\bwhat\s+source(?:s)?\b.*\b(?:used|use|based on)\b", re.IGNORECASE),
            re.compile(r"\btell me\b.*\bwhich\s+document\b", re.IGNORECASE),
            re.compile(r"\btell me\b.*\bwhich\s+source(?:s)?\b", re.IGNORECASE),
        ]

    async def enforce_request_budget(self, rate_limit_key: str) -> None:
        allowed, _ = await self._burst_limiter.check(rate_limit_key)
        if not allowed:
            raise ValueError("Chat rate limit exceeded")

        daily_key = f"rate_limit_daily:{rate_limit_key}:{datetime.now(timezone.utc).date().isoformat()}"
        current = await self._redis.incr(daily_key)
        if current == 1:
            await self._redis.expire(daily_key, 60 * 60 * 48)

        if current > self._settings.chat_daily_limit_requests:
            raise ValueError("Chat daily quota exceeded")

    def validate_user_message(self, message: str, recent_user_messages: list[str]) -> str:
        normalized = self._normalize(message)
        if len(normalized) > self._settings.chat_max_message_chars:
            raise ValueError("Prompt too long")
        if self._estimate_tokens(normalized) > self._settings.chat_max_input_tokens:
            raise ValueError("Prompt too long")

        if self._matches_blocked_pattern(normalized):
            raise ValueError("Your request matches a blocked pattern. Please rephrase it.")

        if self._matches_source_introspection_pattern(normalized):
            raise ValueError(SOURCE_INTROSPECTION_REFUSAL_TEXT)

        if self._is_repeated_prompt(normalized, recent_user_messages):
            raise ValueError("Repeated or highly similar prompts are blocked. Please rephrase your request.")

        return normalized

    def clamp_top_k(self, requested_top_k: int) -> int:
        return max(CHAT_MIN_TOP_K, min(requested_top_k, CHAT_MAX_TOP_K))

    def limit_history(self, messages: list[ChatMessage]) -> list[ChatMessage]:
        if self._settings.chat_max_history_messages <= 0:
            return []
        return messages[-self._settings.chat_max_history_messages :]

    def truncate_response(self, text: str) -> str:
        max_chars = CHAT_MAX_RESPONSE_CHARS
        max_tokens = CHAT_MAX_RESPONSE_TOKENS
        if len(text) <= max_chars and self._estimate_tokens(text) <= max_tokens:
            return self._strip_terminal_decorations(text)

        # Cut the original text (not a re-joined word list) so line breaks, lists
        # and tables survive.
        limit = max_chars
        words = list(re.finditer(r"\S+", text))
        if len(words) > max_tokens:
            limit = min(limit, words[max_tokens - 1].end())
        limited = text[:limit].rstrip()
        if not limited:
            return ""

        natural = self._trim_to_natural_boundary(limited)
        if natural:
            return self._strip_terminal_decorations(natural)

        if len(limited) >= max_chars and max_chars > 3:
            shortened = limited[: max_chars - 3].rstrip(" ,;:-") + "..."
            return self._strip_terminal_decorations(shortened)

        return self._strip_terminal_decorations(limited.rstrip(" ,;:-") + "...")

    def _matches_blocked_pattern(self, message: str) -> bool:
        return any(pattern.search(message) for pattern in self._blocked_patterns)

    def _matches_source_introspection_pattern(self, message: str) -> bool:
        return any(pattern.search(message) for pattern in self._source_introspection_patterns)

    def _is_repeated_prompt(self, message: str, recent_user_messages: list[str]) -> bool:
        # Short messages ("tell me more", "why?") are normal to repeat in a conversation.
        if len(message) < _MIN_REPEAT_CHECK_CHARS:
            return False

        normalized_recent = [self._normalize(item) for item in recent_user_messages if self._normalize(item)]
        if not normalized_recent:
            return False

        lookback = normalized_recent[-CHAT_REPEATED_PROMPT_LOOKBACK:]
        identical_count = sum(1 for item in lookback if item == message)
        if identical_count >= 2:
            return True

        near_matches = 0
        for previous in lookback:
            if len(previous) < _MIN_REPEAT_CHECK_CHARS:
                continue
            ratio = SequenceMatcher(None, message, previous).ratio()
            if ratio >= 0.92:
                near_matches += 1
            if near_matches >= 2:
                return True

        return False

    def _normalize(self, text: str) -> str:
        return " ".join(text.strip().split())

    def _estimate_tokens(self, text: str) -> int:
        if not text:
            return 0
        return len(text.split())

    def _trim_to_natural_boundary(self, text: str) -> str:
        boundary_chars = ".!?\n"
        boundary_index = -1
        for char in boundary_chars:
            index = text.rfind(char)
            if index > boundary_index:
                boundary_index = index

        if boundary_index <= 0:
            return ""

        trimmed = text[: boundary_index + 1].rstrip()
        if not trimmed:
            return ""

        return trimmed

    def _strip_terminal_decorations(self, text: str) -> str:
        stripped = text.rstrip()
        while stripped:
            last_char = stripped[-1]
            if last_char in {".", "!", "?", ",", ";", ":"}:
                return stripped

            if last_char in {"\uFE0E", "\uFE0F"}:
                stripped = stripped[:-1].rstrip()
                continue

            if unicodedata.category(last_char) in {"So", "Sk"}:
                stripped = stripped[:-1].rstrip()
                continue

            break

        return stripped
