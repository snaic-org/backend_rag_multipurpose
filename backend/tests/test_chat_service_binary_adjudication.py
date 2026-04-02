from __future__ import annotations

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

from app.services.chat_service import _parse_binary_adjudication_answer


def test_parse_binary_adjudication_answer_accepts_plain_json() -> None:
    raw = '{"answer":"Yes","reason":"Both clauses are supported."}'
    assert _parse_binary_adjudication_answer(raw) == "Yes"


def test_parse_binary_adjudication_answer_accepts_wrapped_json() -> None:
    raw = '```json\n{"answer":"No","reason":"One clause is unsupported."}\n```'
    assert _parse_binary_adjudication_answer(raw) == "No"


def test_parse_binary_adjudication_answer_ignores_insufficient() -> None:
    raw = '{"answer":"Insufficient","reason":"The evidence is incomplete."}'
    assert _parse_binary_adjudication_answer(raw) is None
