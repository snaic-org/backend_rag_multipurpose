import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import auth as auth_module
from app.core.config import Settings
from app.core.dashboard import mount_dashboard
from app.services.analytics_service import ESTIMATED_PROMPT_CHARS, build_analytics

NOW = datetime(2026, 11, 2, 6, 0, tzinfo=timezone.utc)  # 2 pm in Singapore


def _row(hours_ago: float, *, status="completed", fallback=False, session="s1", message="How can I collaborate with SNAIC?",
         answer_chars=400, metadata=None, error=None):
    return {
        "created_at": NOW - timedelta(hours=hours_ago),
        "status": status,
        "used_fallback": fallback,
        "session_id": session,
        "provider": "nim",
        "model": "nvidia/model-a",
        "request_message": message,
        "answer_chars": answer_chars,
        "error_message": error,
        "metadata": metadata or {},
    }


def test_analytics_totals_daily_and_comparison() -> None:
    rows = [
        _row(1, metadata={"duration_ms": 2000, "input_chars": 8000, "output_chars": 400}),
        _row(2, session="s2", metadata={"duration_ms": 4000, "input_chars": 4000, "output_chars": 800}),
        _row(30, fallback=True, session="s3", message="Do you have internships?"),
        _row(50, status="failed", session="s3", error="Upstream model timed out", answer_chars=0),
    ]
    previous = [_row(24 * 8, session="old")]
    feedback = [
        SimpleNamespace(rating=5, comments="Very helpful collaboration answer", created_at=NOW - timedelta(hours=3)),
        SimpleNamespace(rating=2, comments="", created_at=NOW - timedelta(hours=4)),
    ]

    result = build_analytics(rows, previous, feedback, start_at=NOW - timedelta(days=7), end_at=NOW, days=7)

    assert result.totals.chats == 4 and result.totals.completed == 3 and result.totals.failed == 1
    assert result.totals.fallback == 1 and result.totals.sessions == 3
    assert result.previous_totals.chats == 1
    # Measured chats use their recorded sizes; unmeasured answered chats use the typical prompt
    # size; fallback and failed chats made no model call.
    assert result.totals.input_tokens == 2000 + 1000 + 0 + 0
    assert result.totals.output_tokens == 100 + 200 + 100 + 0
    assert result.tokens_measured_share == 0.5
    assert len(result.daily) == 8 and result.daily[-1].date == "2026-11-02"
    assert sum(day.chats for day in result.daily) == 4
    assert result.hourly[13] == 1 and result.hourly[12] == 2  # Singapore hours: 1 pm, and noon twice
    assert result.latency.samples == 2 and result.latency.average_ms == 3000 and result.latency.p95_ms == 4000
    assert result.keywords[0].label == "collaborate" and result.keywords[0].count == 3
    assert result.top_questions[0].label == "How can I collaborate with SNAIC?"
    assert result.errors[0].label == "Upstream model timed out"
    assert result.feedback.count == 2 and result.feedback.average_rating == 3.5
    assert result.feedback.distribution == {"1": 0, "2": 1, "3": 0, "4": 0, "5": 1}
    assert [comment.rating for comment in result.feedback.recent_comments] == [5]


def test_unmeasured_answer_uses_typical_prompt_size() -> None:
    result = build_analytics([_row(1, message="Hi")], [], [], start_at=NOW - timedelta(days=1), end_at=NOW, days=1)

    assert result.totals.input_tokens == -(-(ESTIMATED_PROMPT_CHARS + 2) // 4)
    assert result.latency.samples == 0 and result.latency.average_ms is None


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}

    async def get(self, key):
        return self.values.get(key)

    async def incr(self, key):
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def expire(self, key, seconds):
        return True


class FakeAuthService:
    async def issue_access_token(self, username: str, password: str):
        if password != "right":
            raise ValueError("Invalid username or password")
        return {
            "access_token": "token",
            "token_type": "bearer",
            "expires_in_seconds": 3600,
            "user": {"id": "00000000-0000-0000-0000-000000000001", "username": username, "is_admin": True, "auth_type": "bearer"},
        }


def _auth_app(redis) -> FastAPI:
    app = FastAPI()
    app.include_router(auth_module.router, prefix="/auth")
    app.state.auth_service = FakeAuthService()
    app.state.redis = SimpleNamespace(client=redis)
    return app


def test_repeated_failed_sign_ins_are_throttled() -> None:
    redis = FakeRedis()
    client = TestClient(_auth_app(redis))

    for _ in range(auth_module.LOGIN_FAILURE_LIMIT):
        assert client.post("/auth/token", json={"username": "admin", "password": "wrong"}).status_code == 401
    blocked = client.post("/auth/token", json={"username": "admin", "password": "right"})

    assert blocked.status_code == 429
    assert "Too many failed sign-in attempts" in blocked.json()["detail"]


def test_sign_in_works_when_redis_is_down() -> None:
    class BrokenRedis:
        async def get(self, key):
            raise ConnectionError("redis down")

        async def incr(self, key):
            raise ConnectionError("redis down")

    client = TestClient(_auth_app(BrokenRedis()))

    assert client.post("/auth/token", json={"username": "admin", "password": "wrong"}).status_code == 401
    assert client.post("/auth/token", json={"username": "admin", "password": "right"}).status_code == 200


def test_dashboard_is_served_with_strict_headers() -> None:
    app = FastAPI()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    mount_dashboard(app)
    client = TestClient(app)

    page = client.get("/dashboard/")
    assert page.status_code == 200
    assert "SNAIC Console" in page.text
    assert "script-src 'self'" in page.headers["content-security-policy"]
    assert page.headers["x-frame-options"] == "DENY"
    assert client.get("/dashboard/assets/app.js").status_code == 200
    # API responses are left alone.
    assert "content-security-policy" not in client.get("/health").headers


def test_analytics_endpoint_requires_admin_and_bounds_days() -> None:
    from app.api import admin as admin_module
    from app.core.security import require_admin_user

    app = FastAPI()
    app.include_router(admin_module.router, prefix="/admin")
    calls = []

    class FakeAnalytics:
        async def summarize(self, days):
            calls.append(days)
            return build_analytics([], [], [], start_at=NOW - timedelta(days=days), end_at=NOW, days=days)

    app.state.analytics_service = FakeAnalytics()
    app.state.settings = Settings(auth_require_https=False)
    app.state.auth_service = SimpleNamespace()
    app.dependency_overrides[require_admin_user] = lambda: SimpleNamespace(is_admin=True)
    client = TestClient(app)

    assert client.get("/admin/analytics?days=7").status_code == 200
    assert client.get("/admin/analytics?days=0").status_code == 422
    assert client.get("/admin/analytics?days=400").status_code == 422
    assert calls == [7]

    app.dependency_overrides.clear()
    assert client.get("/admin/analytics").status_code in (401, 403)


def test_analytics_service_splits_current_and_previous_period() -> None:
    from app.services import analytics_service

    service = analytics_service.AnalyticsService.__new__(analytics_service.AnalyticsService)
    recorded = {}

    class Activity:
        async def list_for_analytics(self, *, start_at, end_at, limit):
            recorded.update(start_at=start_at, end_at=end_at, limit=limit)
            return [_row(1), _row(24 * 10)]

    class Feedback:
        async def list_feedback(self, *, limit, start_at, end_at):
            return []

    service._activity = Activity()
    service._feedback = Feedback()

    result = asyncio.run(service.summarize(7, now=NOW))

    assert recorded["start_at"] == NOW - timedelta(days=14)
    assert result.totals.chats == 1 and result.previous_totals.chats == 1
