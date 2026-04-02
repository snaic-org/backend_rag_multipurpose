from __future__ import annotations

import re

from app.core.config import Settings


class QueryPlannerService:
    _SPLIT_PATTERNS = [
        r",\s+and\s+the subsequent\s+",
        r",\s+and\s+(?=(?:the\s+)?(?:subsequent|later|another)\b)",
        r",\s+and\s+(?=(?:did|does|do|was|were|is|are|has|have)\b)",
        r"\s+while\s+",
        r"\s+compared to\s+",
        r"\s+compared with\s+",
        r"\s+versus\s+",
        r"\s+vs\.?\s+",
    ]
    _ANCHOR_PATTERN = re.compile(
        r"[^,;:]*\b(article|report|coverage|story|published|regarding|concerning|discusses?|suggests?|indicates?)\b[^,;:]*",
        flags=re.IGNORECASE,
    )
    _LEADING_AUXILIARY_PATTERN = re.compile(
        r"^(do|does|did|is|are|was|were|has|have|had|can|could|would|should|will)\s+",
        flags=re.IGNORECASE,
    )

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def build_queries(self, query_text: str) -> list[str]:
        normalized = self._normalize(query_text)
        if not normalized:
            return []

        if not self._settings.retrieval_multi_query_enabled:
            return [normalized]

        max_queries = max(1, self._settings.retrieval_multi_query_max_queries)
        queries = [normalized]
        seen_queries = {normalized.lower()}

        for candidate in self._candidate_queries(normalized):
            cleaned = self._clean_candidate(candidate)
            if not self._is_useful_candidate(cleaned):
                continue
            lowered = cleaned.lower()
            if lowered in seen_queries:
                continue
            queries.append(cleaned)
            seen_queries.add(lowered)
            if len(queries) >= max_queries:
                break

        return queries

    def _candidate_queries(self, query_text: str) -> list[str]:
        candidates: list[str] = []

        for pattern in self._SPLIT_PATTERNS:
            parts = [self._normalize(part) for part in re.split(pattern, query_text, maxsplit=1, flags=re.IGNORECASE)]
            if len(parts) < 2:
                continue
            candidates.extend(part for part in parts if part)

        for match in self._ANCHOR_PATTERN.finditer(query_text):
            candidates.append(match.group(0))

        return candidates

    def _clean_candidate(self, candidate: str) -> str:
        normalized = self._normalize(candidate)
        normalized = normalized.rstrip("?.!,;: ")
        normalized = self._LEADING_AUXILIARY_PATTERN.sub("", normalized)
        return normalized.strip()

    def _is_useful_candidate(self, candidate: str) -> bool:
        if not candidate:
            return False
        if len(candidate) < 30:
            return False
        if len(candidate.split()) < 5:
            return False
        return True

    def _normalize(self, value: str) -> str:
        return " ".join(value.strip().split())
