"""Usage analytics for the admin dashboard, built from chat activity and feedback logs."""

import math
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from psycopg_pool import AsyncConnectionPool

from app.db.repositories.chat_activity import ChatActivityRepository
from app.db.repositories.chat_feedback import ChatFeedbackRepository
from app.models.schemas import (
    AnalyticsCount,
    AnalyticsDay,
    AnalyticsFeedback,
    AnalyticsFeedbackComment,
    AnalyticsLatency,
    AnalyticsResponse,
    AnalyticsTotals,
)
from app.services.text_terms import STOPWORDS, significant_terms

ANALYTICS_TIMEZONE = "Asia/Singapore"
# Token counts are estimated from text length; ~4 characters per token is the
# usual rule of thumb for English with these tokenizers.
CHARS_PER_TOKEN = 4
# Chats logged before prompt sizes were recorded: system prompt, retrieved
# context and history come to roughly this many characters per request.
ESTIMATED_PROMPT_CHARS = 12000
# Upper bound on rows read per request; the response says when it was reached.
MAX_ACTIVITY_ROWS = 20000
MAX_FEEDBACK_ROWS = 5000

# Words too common in questions to say anything about what people ask.
_KEYWORD_STOPWORDS = STOPWORDS | {
    "about", "any", "are", "could", "did", "give", "has", "have", "know", "like", "list",
    "many", "more", "much", "our", "please", "snaic", "some", "than", "that", "the",
    "their", "them", "then", "there", "these", "they", "this", "those", "was", "were",
    "will", "would", "your", "can", "get", "also", "just", "not", "all", "its", "into",
    "want", "need", "there", "here", "very", "really", "thank", "thanks", "hello", "hi",
}
_QUESTION_NORMALIZER = re.compile(r"[^a-z0-9 ]+")


class AnalyticsService:
    def __init__(self, postgres_pool: AsyncConnectionPool) -> None:
        self._activity = ChatActivityRepository(postgres_pool)
        self._feedback = ChatFeedbackRepository(postgres_pool)

    async def summarize(self, days: int, now: datetime | None = None) -> AnalyticsResponse:
        end_at = now or datetime.now(timezone.utc)
        start_at = end_at - timedelta(days=days)
        previous_start = start_at - timedelta(days=days)

        rows = await self._activity.list_for_analytics(
            start_at=previous_start, end_at=end_at, limit=MAX_ACTIVITY_ROWS
        )
        feedback = await self._feedback.list_feedback(limit=MAX_FEEDBACK_ROWS, start_at=start_at, end_at=end_at)
        current = [row for row in rows if row["created_at"] >= start_at]
        previous = [row for row in rows if row["created_at"] < start_at]
        return build_analytics(
            current,
            previous,
            feedback,
            start_at=start_at,
            end_at=end_at,
            days=days,
            truncated=len(rows) >= MAX_ACTIVITY_ROWS,
        )


def build_analytics(
    rows: list[dict],
    previous_rows: list[dict],
    feedback: list,
    *,
    start_at: datetime,
    end_at: datetime,
    days: int,
    truncated: bool = False,
) -> AnalyticsResponse:
    zone = ZoneInfo(ANALYTICS_TIMEZONE)

    daily: dict[str, AnalyticsDay] = {}
    first_day = start_at.astimezone(zone).date()
    last_day = end_at.astimezone(zone).date()
    day = first_day
    while day <= last_day:
        daily[day.isoformat()] = AnalyticsDay(date=day.isoformat())
        day += timedelta(days=1)
    daily_sessions: dict[str, set[str]] = {key: set() for key in daily}

    hourly = [0] * 24
    weekday_hourly = [[0] * 24 for _ in range(7)]  # Monday first
    models: Counter[str] = Counter()
    keywords: Counter[str] = Counter()
    questions: Counter[str] = Counter()
    question_text: dict[str, str] = {}
    errors: Counter[str] = Counter()
    durations: list[int] = []
    measured = 0
    answer_chars: list[int] = []

    for row in rows:
        local = row["created_at"].astimezone(zone)
        bucket = daily.get(local.date().isoformat())
        input_tokens, output_tokens, was_measured = _token_estimate(row)
        measured += was_measured
        if bucket is not None:
            bucket.chats += 1
            bucket.completed += row["status"] == "completed"
            bucket.failed += row["status"] == "failed"
            bucket.fallback += bool(row["used_fallback"])
            bucket.input_tokens += input_tokens
            bucket.output_tokens += output_tokens
            if row.get("session_id"):
                daily_sessions[bucket.date].add(row["session_id"])
        hourly[local.hour] += 1
        weekday_hourly[local.weekday()][local.hour] += 1

        if row.get("model"):
            models[row["model"]] += 1
        message = row.get("request_message") or ""
        keywords.update(set(_keywords(message)))
        normalized = " ".join(_QUESTION_NORMALIZER.sub(" ", message.lower()).split())
        if normalized:
            questions[normalized] += 1
            question_text.setdefault(normalized, message.strip()[:200])
        if row["status"] == "failed" and row.get("error_message"):
            errors[row["error_message"].strip()[:160]] += 1
        duration = (row.get("metadata") or {}).get("duration_ms")
        if isinstance(duration, (int, float)) and duration >= 0:
            durations.append(int(duration))
        if row["status"] == "completed":
            answer_chars.append(int(row.get("answer_chars") or 0))

    for key, bucket in daily.items():
        bucket.sessions = len(daily_sessions[key])

    ratings = [int(item.rating) for item in feedback]
    comments = [item for item in feedback if (item.comments or "").strip()]
    comment_keywords: Counter[str] = Counter()
    for item in comments:
        comment_keywords.update(set(_keywords(item.comments)))

    return AnalyticsResponse(
        start_at=start_at,
        end_at=end_at,
        days=days,
        timezone=ANALYTICS_TIMEZONE,
        totals=_totals(rows),
        previous_totals=_totals(previous_rows),
        daily=list(daily.values()),
        hourly=hourly,
        weekday_hourly=weekday_hourly,
        models=_top(models, 8),
        keywords=_top(keywords, 20),
        top_questions=[
            AnalyticsCount(label=question_text[key], count=count) for key, count in questions.most_common(10)
        ],
        errors=_top(errors, 5),
        latency=_latency(durations),
        tokens_measured_share=round(measured / len(rows), 3) if rows else 0.0,
        average_answer_chars=round(sum(answer_chars) / len(answer_chars)) if answer_chars else 0,
        feedback=AnalyticsFeedback(
            count=len(ratings),
            average_rating=round(sum(ratings) / len(ratings), 2) if ratings else None,
            distribution={str(score): ratings.count(score) for score in range(1, 6)},
            keywords=_top(comment_keywords, 12),
            recent_comments=[
                AnalyticsFeedbackComment(rating=item.rating, comments=item.comments.strip()[:500], created_at=item.created_at)
                for item in sorted(comments, key=lambda item: item.created_at, reverse=True)[:10]
            ],
        ),
        truncated=truncated,
    )


def _token_estimate(row: dict) -> tuple[int, int, bool]:
    """Input and output tokens for one chat, and whether the prompt size was recorded."""
    metadata = row.get("metadata") or {}
    input_chars = metadata.get("input_chars")
    output_chars = metadata.get("output_chars")
    measured = isinstance(input_chars, int)
    if not measured:
        # No model call happened for a chat that failed before generation.
        reached_model = row["status"] == "completed" and not row["used_fallback"]
        input_chars = ESTIMATED_PROMPT_CHARS + len(row.get("request_message") or "") if reached_model else 0
    if not isinstance(output_chars, int):
        output_chars = int(row.get("answer_chars") or 0)
    return math.ceil(input_chars / CHARS_PER_TOKEN), math.ceil(output_chars / CHARS_PER_TOKEN), measured


def _totals(rows: list[dict]) -> AnalyticsTotals:
    totals = AnalyticsTotals(chats=len(rows))
    sessions: set[str] = set()
    for row in rows:
        totals.completed += row["status"] == "completed"
        totals.failed += row["status"] == "failed"
        totals.fallback += bool(row["used_fallback"])
        if row.get("session_id"):
            sessions.add(row["session_id"])
        input_tokens, output_tokens, _ = _token_estimate(row)
        totals.input_tokens += input_tokens
        totals.output_tokens += output_tokens
    totals.sessions = len(sessions)
    return totals


def _keywords(text: str) -> list[str]:
    return [term for term in significant_terms(text, min_length=3) if term not in _KEYWORD_STOPWORDS and not term.isdigit()]


def _top(counter: Counter[str], limit: int) -> list[AnalyticsCount]:
    return [AnalyticsCount(label=label, count=count) for label, count in counter.most_common(limit)]


def _latency(durations: list[int]) -> AnalyticsLatency:
    if not durations:
        return AnalyticsLatency()
    ordered = sorted(durations)

    def percentile(share: float) -> int:
        return ordered[min(len(ordered) - 1, math.ceil(share * len(ordered)) - 1)]

    return AnalyticsLatency(
        samples=len(ordered),
        average_ms=round(sum(ordered) / len(ordered)),
        p50_ms=percentile(0.5),
        p95_ms=percentile(0.95),
    )
