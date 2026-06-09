import sys
import types

redis_module = types.ModuleType("redis")
redis_asyncio_module = types.ModuleType("redis.asyncio")
redis_asyncio_module.Redis = object
redis_module.asyncio = redis_asyncio_module
sys.modules.setdefault("redis", redis_module)
sys.modules.setdefault("redis.asyncio", redis_asyncio_module)

psycopg_pool_module = types.ModuleType("psycopg_pool")
psycopg_pool_module.AsyncConnectionPool = object
sys.modules.setdefault("psycopg_pool", psycopg_pool_module)

psycopg_module = types.ModuleType("psycopg")
psycopg_rows_module = types.ModuleType("psycopg.rows")
psycopg_rows_module.dict_row = object
psycopg_types_module = types.ModuleType("psycopg.types")
psycopg_types_json_module = types.ModuleType("psycopg.types.json")
psycopg_types_json_module.Jsonb = object
psycopg_module.rows = psycopg_rows_module
psycopg_module.types = psycopg_types_module
sys.modules.setdefault("psycopg", psycopg_module)
sys.modules.setdefault("psycopg.rows", psycopg_rows_module)
sys.modules.setdefault("psycopg.types", psycopg_types_module)
sys.modules.setdefault("psycopg.types.json", psycopg_types_json_module)

from app.models.schemas import ChatMessage
from app.services.chat_service import ChatService


def test_chat_service_uses_explicit_public_session_id() -> None:
    service = ChatService.__new__(ChatService)

    assert service._resolve_session_id("public-session-123") == "public-session-123"
    assert service._resolve_session_id(None) is None


def test_chat_service_expands_short_follow_up_retrieval_query_with_history() -> None:
    service = ChatService.__new__(ChatService)
    history = [
        ChatMessage(role="user", content="how can snaic collab"),
        ChatMessage(
            role="assistant",
            content=(
                "The collaboration process with SNAIC has 6 stages\n"
                "1. Pre-engagement questionnaire\n"
                "2. 1-to-1 consultation\n"
                "3. Project scoping"
            ),
        ),
    ]

    retrieval_message = service._build_retrieval_message("tell me more about 3", history)

    assert "Project scoping" in retrieval_message
    assert "user follow-up: tell me more about 3" in retrieval_message


def test_chat_service_keeps_standalone_retrieval_query_clean() -> None:
    service = ChatService.__new__(ChatService)
    history = [ChatMessage(role="assistant", content="Project scoping")]

    retrieval_message = service._build_retrieval_message(
        "What real-time video analytics services does SNAIC provide?",
        history,
    )

    assert retrieval_message == "What real-time video analytics services does SNAIC provide?"


def test_chat_service_strips_user_question_preamble_from_final_answer() -> None:
    service = ChatService.__new__(ChatService)

    answer = service._strip_user_question_preamble(
        "The user's question \"tell me about 3\" refers to the third stage. "
        "Project scoping defines the project goals, scope, timeline, and expected outcomes."
    )

    assert answer == "Project scoping defines the project goals, scope, timeline, and expected outcomes."
