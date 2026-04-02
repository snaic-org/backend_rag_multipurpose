#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import math
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

import httpx
from rapidfuzz import fuzz

from config import EVAL_DEFAULTS, HF_DATASET_PRESETS


DEFAULT_HF_DATASET = str(EVAL_DEFAULTS["hf_dataset"])
DEFAULT_HF_CONFIG = str(EVAL_DEFAULTS["hf_config"])
DEFAULT_HF_SPLIT = str(EVAL_DEFAULTS["hf_split"])
DEFAULT_CONTEXT_MATCH_THRESHOLD = float(EVAL_DEFAULTS["context_match_threshold"])
DEFAULT_ANSWER_MATCH_THRESHOLD = float(EVAL_DEFAULTS["answer_match_threshold"])
DEFAULT_JUDGE_TIMEOUT_SECONDS = float(EVAL_DEFAULTS["judge_timeout_seconds"])
VALID_JUDGE_METRICS = {"correctness", "relevance", "groundedness", "embedding"}
KNOWN_HF_DATASETS = {
    preset["hf_dataset"]: {
        "hf_config": preset["hf_config"],
        "hf_split": preset["hf_split"],
    }
    for preset in HF_DATASET_PRESETS.values()
}


@dataclass
class BenchmarkSample:
    sample_id: str
    question: str
    ground_truth: str
    reference_contexts: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvalRunConfig:
    base_url: str
    username: str
    password: str
    dataset_source: str
    dataset_path: str | None
    hf_dataset: str
    hf_config: str | None
    hf_split: str
    hf_trust_remote_code: bool
    sample_size: int
    top_k: int
    batch_size: int
    reset_first: bool
    force_reingest: bool
    embedding_provider: str | None
    embedding_model: str | None
    generation_provider: str | None
    generation_model: str | None
    eval_llm_model: str | None
    eval_llm_base_url: str | None
    eval_llm_api_key: str | None
    eval_embedding_model: str | None
    eval_embedding_base_url: str | None
    eval_embedding_api_key: str | None
    judge_metrics: list[str]
    judge_max_rpm: int
    output_dir: str


class JudgeRateLimiter:
    def __init__(self, max_rpm: int) -> None:
        self._max_rpm = max(1, max_rpm)
        self._interval_seconds = 60.0 / float(self._max_rpm)
        self._last_request_started_at: float | None = None

    def wait(self, *, sample_id: str, sample_index: int, total_samples: int, metric_name: str) -> None:
        now = time.monotonic()
        if self._last_request_started_at is not None:
            elapsed = now - self._last_request_started_at
            remaining = self._interval_seconds - elapsed
            if remaining > 0:
                print(
                    f"Judge throttle: waiting {remaining:.1f}s before {metric_name} "
                    f"for {sample_id} ({sample_index}/{total_samples})"
                )
                time.sleep(remaining)
        self._last_request_started_at = time.monotonic()
        print(f"Judge request: {metric_name} for {sample_id} ({sample_index}/{total_samples})")


class BackendRagClient:
    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        timeout_seconds: float = 120.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=timeout_seconds)
        self._headers: dict[str, str] = {}

    async def __aenter__(self) -> "BackendRagClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self._client.aclose()

    async def login(self) -> None:
        response = await self._client.post(
            "/auth/token",
            json={"username": self._username, "password": self._password},
        )
        response.raise_for_status()
        payload = response.json()
        token = payload.get("access_token")
        if not isinstance(token, str) or not token.strip():
            raise RuntimeError(f"Login succeeded but no access token was returned: {payload}")
        self._headers = {"Authorization": f"Bearer {token}"}

    async def reset(self) -> dict[str, Any]:
        response = await self._client.delete("/admin/reset", headers=self._headers)
        response.raise_for_status()
        return response.json()

    async def health(self) -> dict[str, Any]:
        response = await self._client.get("/health")
        response.raise_for_status()
        return response.json()

    async def get_model_selection(self) -> dict[str, Any]:
        response = await self._client.get("/admin/model-selection", headers=self._headers)
        response.raise_for_status()
        return response.json()

    async def ingest_text_items(
        self,
        *,
        items: list[dict[str, Any]],
        embedding_provider: str | None,
        embedding_model: str | None,
        force_reingest: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "items": items,
            "force_reingest": force_reingest,
        }
        if embedding_provider:
            payload["embedding_provider"] = embedding_provider
        if embedding_model:
            payload["embedding_model"] = embedding_model

        response = await self._client.post("/ingest/text", headers=self._headers, json=payload)
        response.raise_for_status()
        return response.json()

    async def chat(
        self,
        *,
        message: str,
        top_k: int,
        generation_provider: str | None,
        generation_model: str | None,
        embedding_provider: str | None,
        embedding_model: str | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "message": message,
            "debug": True,
            "top_k": top_k,
        }
        if generation_provider:
            payload["provider"] = generation_provider
        if generation_model:
            payload["model"] = generation_model
        if embedding_provider:
            payload["embedding_provider"] = embedding_provider
        if embedding_model:
            payload["embedding_model"] = embedding_model

        response = await self._client.post("/chat", headers=self._headers, json=payload)
        response.raise_for_status()
        return response.json()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate the backend RAG chatbot with LangChain evaluators."
    )
    parser.add_argument("--base-url", default=os.environ.get("BASE_URL"), help="Backend base URL.")
    parser.add_argument("--username", default=os.environ.get("RAG_EVAL_USERNAME"))
    parser.add_argument("--password", default=os.environ.get("RAG_EVAL_PASSWORD"))
    parser.add_argument(
        "--dataset-source",
        choices=["huggingface", "jsonl"],
        default=str(EVAL_DEFAULTS["dataset_source"]),
    )
    parser.add_argument(
        "--dataset-path",
        default=str(EVAL_DEFAULTS["dataset_path"]),
        help="Path to a local JSONL benchmark.",
    )
    parser.add_argument("--hf-dataset", default=DEFAULT_HF_DATASET)
    parser.add_argument("--hf-config", default=DEFAULT_HF_CONFIG)
    parser.add_argument("--hf-split", default=DEFAULT_HF_SPLIT)
    parser.add_argument(
        "--hf-trust-remote-code",
        action="store_true",
        default=bool(EVAL_DEFAULTS["hf_trust_remote_code"]),
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=int(EVAL_DEFAULTS["sample_size"]),
        help="Max samples to evaluate.",
    )
    parser.add_argument("--top-k", type=int, default=int(EVAL_DEFAULTS["top_k"]))
    parser.add_argument(
        "--batch-size",
        type=int,
        default=int(EVAL_DEFAULTS["batch_size"]),
        help="Ingest batch size.",
    )
    parser.add_argument(
        "--reset-first",
        action="store_true",
        default=bool(EVAL_DEFAULTS["reset_first"]),
        help="Call /admin/reset before ingest.",
    )
    parser.add_argument(
        "--force-reingest",
        action="store_true",
        default=bool(EVAL_DEFAULTS["force_reingest"]),
        help="Pass force_reingest=true.",
    )
    parser.add_argument("--embedding-provider", default=str(EVAL_DEFAULTS["embedding_provider"]))
    parser.add_argument("--embedding-model", default=str(EVAL_DEFAULTS["embedding_model"]))
    parser.add_argument("--generation-provider", default=str(EVAL_DEFAULTS["generation_provider"]))
    parser.add_argument("--generation-model", default=str(EVAL_DEFAULTS["generation_model"]))
    parser.add_argument(
        "--judge-metrics",
        default=str(EVAL_DEFAULTS["judge_metrics"]),
        help="Comma-separated judge metrics: all, none, correctness, relevance, groundedness, embedding.",
    )
    parser.add_argument(
        "--judge-max-rpm",
        type=int,
        default=int(EVAL_DEFAULTS["judge_max_rpm"]),
        help="Maximum judge-side requests per minute. Use below provider limits for headroom.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory for raw outputs. Defaults to eval/output/<timestamp>.",
    )
    return parser.parse_args()


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def env_or_backend(backend_env: dict[str, str], env_key: str, backend_key: str | None = None) -> str | None:
    value = os.environ.get(env_key)
    if value:
        return value
    if backend_key:
        candidate = backend_env.get(backend_key)
        if candidate:
            return candidate
    return None


def parse_judge_metrics(raw_value: str) -> list[str]:
    normalized = normalize_text(raw_value).lower()
    if not normalized or normalized == "all":
        return sorted(VALID_JUDGE_METRICS)
    if normalized == "none":
        return []

    requested = {item.strip().lower() for item in normalized.split(",") if item.strip()}
    invalid = sorted(requested - VALID_JUDGE_METRICS)
    if invalid:
        raise RuntimeError(
            f"Invalid --judge-metrics value(s): {', '.join(invalid)}. "
            f"Valid options: all, none, {', '.join(sorted(VALID_JUDGE_METRICS))}."
        )
    return sorted(requested)


def resolve_hf_dataset_settings(
    dataset_source: str,
    hf_dataset: str,
    hf_config: str | None,
    hf_split: str | None,
) -> tuple[str, str | None, str]:
    normalized_dataset = normalize_text(hf_dataset)
    normalized_config = normalize_text(hf_config)
    normalized_split = normalize_text(hf_split)

    if dataset_source != "huggingface":
        return normalized_dataset, normalized_config or None, normalized_split or DEFAULT_HF_SPLIT

    known = KNOWN_HF_DATASETS.get(normalized_dataset)
    if known is None:
        return normalized_dataset, normalized_config or None, normalized_split or DEFAULT_HF_SPLIT

    expected_config = normalize_text(known["hf_config"])
    expected_split = normalize_text(known["hf_split"])

    if normalized_config and normalized_config != expected_config:
        raise RuntimeError(
            f"Invalid hf_config '{normalized_config}' for dataset '{normalized_dataset}'. "
            f"Configured presets expect hf_config='{expected_config}'."
        )

    resolved_config = normalized_config or expected_config or None
    resolved_split = normalized_split or expected_split or DEFAULT_HF_SPLIT
    return normalized_dataset, resolved_config, resolved_split


def build_config(args: argparse.Namespace) -> EvalRunConfig:
    repo_root = Path(__file__).resolve().parents[1]
    backend_env = load_env_file(repo_root / "backend" / ".env")

    base_url = args.base_url
    if not base_url:
        app_port = backend_env.get("APP_PORT")
        if app_port:
            base_url = f"http://localhost:{app_port}"
        else:
            base_url = "http://localhost:9010"
    if not base_url.startswith(("http://", "https://")):
        base_url = f"http://{base_url}"

    username = args.username or backend_env.get("AUTH_BOOTSTRAP_ADMIN_USERNAME", "admin")
    password = args.password or backend_env.get("AUTH_BOOTSTRAP_ADMIN_PASSWORD")

    output_dir = args.output_dir
    if not output_dir:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_dir = str(Path(__file__).resolve().parent / "output" / timestamp)

    if not password:
        raise RuntimeError(
            "Missing password. Set RAG_EVAL_PASSWORD or backend/.env AUTH_BOOTSTRAP_ADMIN_PASSWORD."
        )

    if args.dataset_source == "jsonl" and not args.dataset_path:
        raise RuntimeError("--dataset-path is required when --dataset-source jsonl is used.")

    hf_dataset, hf_config, hf_split = resolve_hf_dataset_settings(
        args.dataset_source,
        args.hf_dataset,
        args.hf_config,
        args.hf_split,
    )

    eval_llm_model = os.environ.get("LANGCHAIN_EVAL_LLM_MODEL") or (
        backend_env.get("DEFAULT_GENERATION_MODEL")
        if backend_env.get("DEFAULT_GENERATION_PROVIDER") == "nim"
        else None
    )
    eval_llm_base_url = (
        env_or_backend(backend_env, "LANGCHAIN_EVAL_LLM_BASE_URL", "NIM_BASE_URL")
        or str(EVAL_DEFAULTS["judge_llm_base_url"])
    )
    eval_llm_api_key = (
        env_or_backend(backend_env, "LANGCHAIN_EVAL_LLM_API_KEY", "NIM_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )

    eval_embedding_model = os.environ.get("LANGCHAIN_EVAL_EMBED_MODEL") or (
        backend_env.get("DEFAULT_EMBEDDING_MODEL")
        if backend_env.get("DEFAULT_EMBEDDING_PROVIDER") == "nim"
        else None
    )
    eval_embedding_base_url = (
        env_or_backend(backend_env, "LANGCHAIN_EVAL_EMBED_BASE_URL", "NIM_BASE_URL")
        or str(EVAL_DEFAULTS["judge_embedding_base_url"])
    )
    eval_embedding_api_key = (
        env_or_backend(backend_env, "LANGCHAIN_EVAL_EMBED_API_KEY", "NIM_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )

    return EvalRunConfig(
        base_url=base_url,
        username=username,
        password=password,
        dataset_source=args.dataset_source,
        dataset_path=args.dataset_path,
        hf_dataset=hf_dataset,
        hf_config=hf_config,
        hf_split=hf_split,
        hf_trust_remote_code=args.hf_trust_remote_code,
        sample_size=args.sample_size,
        top_k=args.top_k,
        batch_size=args.batch_size,
        reset_first=args.reset_first,
        force_reingest=args.force_reingest,
        embedding_provider=args.embedding_provider,
        embedding_model=args.embedding_model,
        generation_provider=args.generation_provider,
        generation_model=args.generation_model,
        eval_llm_model=eval_llm_model,
        eval_llm_base_url=eval_llm_base_url,
        eval_llm_api_key=eval_llm_api_key,
        eval_embedding_model=eval_embedding_model,
        eval_embedding_base_url=eval_embedding_base_url,
        eval_embedding_api_key=eval_embedding_api_key,
        judge_metrics=parse_judge_metrics(args.judge_metrics),
        judge_max_rpm=max(1, args.judge_max_rpm),
        output_dir=output_dir,
    )


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def coerce_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else []
    if isinstance(value, list):
        flattened: list[str] = []
        for item in value:
            if isinstance(item, dict):
                candidate = first_present(item, ["content", "text", "page_content", "body", "snippet"])
                if candidate:
                    flattened.append(candidate)
            else:
                candidate = normalize_text(item)
                if candidate:
                    flattened.append(candidate)
        return flattened
    return []


def evidence_list_to_contexts(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []

    contexts: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue

        fact = normalize_text(item.get("fact"))
        title = normalize_text(item.get("title"))
        source = normalize_text(item.get("source"))
        published_at = normalize_text(item.get("published_at"))

        parts = []
        if title:
            parts.append(f"Title: {title}")
        if source:
            parts.append(f"Source: {source}")
        if published_at:
            parts.append(f"Published At: {published_at}")
        if fact:
            parts.append(fact)

        if parts:
            contexts.append("\n".join(parts))

    return contexts


def first_present(record: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        candidate = normalize_text(record.get(key))
        if candidate:
            return candidate
    return ""


def extract_reference_contexts(record: dict[str, Any]) -> list[str]:
    multihop_contexts = evidence_list_to_contexts(record.get("evidence_list"))
    if multihop_contexts:
        return multihop_contexts

    for key in [
        "reference_contexts",
        "reference_context",
        "retrieved_contexts",
        "ground_truth_contexts",
        "contexts",
        "context",
        "documents",
    ]:
        contexts = coerce_text_list(record.get(key))
        if contexts:
            return contexts
    return []


def sample_from_record(record: dict[str, Any], index: int) -> BenchmarkSample:
    question = first_present(record, ["question", "user_input", "query", "Prompt"])
    ground_truth = first_present(
        record,
        [
            "ground_truth",
            "reference",
            "answer",
            "Answer",
            "reference_answer",
            "ideal_answer",
        ],
    )
    reference_contexts = extract_reference_contexts(record)

    if not question:
        raise ValueError(f"Record {index} is missing question-like fields.")
    if not ground_truth:
        raise ValueError(f"Record {index} is missing answer/reference-like fields.")
    if not reference_contexts:
        raise ValueError(f"Record {index} is missing context/documents fields.")

    sample_id = normalize_text(record.get("id")) or f"sample-{index:04d}"
    return BenchmarkSample(
        sample_id=sample_id,
        question=question,
        ground_truth=ground_truth,
        reference_contexts=reference_contexts,
        metadata={k: v for k, v in record.items() if k not in {"question", "user_input", "query"}},
    )


def load_jsonl_samples(path: Path, limit: int) -> list[BenchmarkSample]:
    samples: list[BenchmarkSample] = []
    skipped_rows = 0
    with path.open("r", encoding="utf-8") as handle:
        for index, line in enumerate(handle, start=1):
            raw = line.strip()
            if not raw:
                continue
            record = json.loads(raw)
            try:
                samples.append(sample_from_record(record, index))
            except ValueError as exc:
                skipped_rows += 1
                if skipped_rows <= 5:
                    print(f"Skipping JSONL record {index}: {exc}")
                continue
            if len(samples) >= limit:
                break
    if skipped_rows:
        print(f"Skipped {skipped_rows} invalid JSONL records while loading samples.")
    return samples


def load_huggingface_samples(
    *,
    dataset_name: str,
    config_name: str | None,
    split_name: str,
    trust_remote_code: bool,
    limit: int,
) -> list[BenchmarkSample]:
    from datasets import load_dataset

    try:
        dataset = load_dataset(
            dataset_name,
            config_name,
            split=split_name,
            trust_remote_code=trust_remote_code,
        )
    except RuntimeError as exc:
        if "Dataset scripts are no longer supported" in str(exc):
            raise RuntimeError(
                "The selected Hugging Face dataset relies on a legacy dataset script, "
                "which the installed `datasets` package no longer supports. "
                "Use a parquet/native dataset such as "
                "`explodinggradients/amnesty_qa` or switch to `--dataset-source jsonl`."
            ) from exc
        raise

    samples: list[BenchmarkSample] = []
    skipped_rows = 0
    for index, row in enumerate(dataset, start=1):
        try:
            samples.append(sample_from_record(dict(row), index))
        except ValueError as exc:
            skipped_rows += 1
            if skipped_rows <= 5:
                print(f"Skipping Hugging Face record {index}: {exc}")
            continue
        if len(samples) >= limit:
            break
    if skipped_rows:
        print(f"Skipped {skipped_rows} invalid Hugging Face records while loading samples.")
    return samples


def load_samples(config: EvalRunConfig) -> list[BenchmarkSample]:
    if config.dataset_source == "jsonl":
        return load_jsonl_samples(Path(config.dataset_path), config.sample_size)
    return load_huggingface_samples(
        dataset_name=config.hf_dataset,
        config_name=config.hf_config,
        split_name=config.hf_split,
        trust_remote_code=config.hf_trust_remote_code,
        limit=config.sample_size,
    )


def build_ingest_items(samples: list[BenchmarkSample]) -> list[dict[str, Any]]:
    items_by_hash: dict[str, dict[str, Any]] = {}
    for sample in samples:
        for context_index, context_text in enumerate(sample.reference_contexts, start=1):
            content = normalize_text(context_text)
            if not content:
                continue
            content_hash = hashlib.sha1(content.encode("utf-8")).hexdigest()
            items_by_hash.setdefault(
                content_hash,
                {
                    "title": f"{sample.sample_id}-ctx-{context_index}",
                    "content": content,
                    "source_type": "text",
                    "metadata": {
                        "source": "langchain_eval",
                        "sample_id": sample.sample_id,
                    },
                },
            )
    return list(items_by_hash.values())


def chunked(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def extract_retrieved_contexts(chat_payload: dict[str, Any]) -> list[str]:
    chunks = chat_payload.get("retrieved_chunks", [])
    if not isinstance(chunks, list):
        return []

    contexts: list[str] = []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        content = normalize_text(chunk.get("content"))
        if content:
            contexts.append(content)
    return contexts


def parse_evaluator_score(payload: dict[str, Any]) -> float | None:
    raw_score = payload.get("score")
    if isinstance(raw_score, bool):
        return float(raw_score)
    if isinstance(raw_score, (int, float)):
        return float(raw_score)

    for candidate in [payload.get("value"), payload.get("text"), payload.get("reasoning")]:
        text = normalize_text(candidate).upper()
        if text in {"Y", "YES", "TRUE", "CORRECT", "PASS"}:
            return 1.0
        if text in {"N", "NO", "FALSE", "INCORRECT", "FAIL"}:
            return 0.0
        match = re.search(r"\b(CORRECT|INCORRECT|YES|NO|TRUE|FALSE|PASS|FAIL)\b", text)
        if match:
            label = match.group(1)
            return 1.0 if label in {"CORRECT", "YES", "TRUE", "PASS"} else 0.0
    return None


def extract_evaluator_label(payload: dict[str, Any]) -> str | None:
    for candidate in [payload.get("value"), payload.get("text"), payload.get("reasoning")]:
        text = normalize_text(candidate)
        if text:
            return text[:500]
    return None


def build_langchain_evaluators(config: EvalRunConfig) -> dict[str, Any]:
    evaluators: dict[str, Any] = {}
    selected_metrics = set(config.judge_metrics)

    llm = None
    if config.eval_llm_model and selected_metrics.intersection({"correctness", "relevance", "groundedness"}):
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(
            model=config.eval_llm_model,
            api_key=config.eval_llm_api_key or "dummy",
            base_url=config.eval_llm_base_url,
            temperature=0,
            timeout=DEFAULT_JUDGE_TIMEOUT_SECONDS,
            max_retries=1,
        )

    embeddings = None
    if config.eval_embedding_model and "embedding" in selected_metrics:
        from langchain_openai import OpenAIEmbeddings

        embeddings = OpenAIEmbeddings(
            model=config.eval_embedding_model,
            api_key=config.eval_embedding_api_key or "dummy",
            base_url=config.eval_embedding_base_url,
            timeout=DEFAULT_JUDGE_TIMEOUT_SECONDS,
            max_retries=1,
        )

    if llm is not None:
        from langchain_classic.evaluation.criteria.eval_chain import (
            CriteriaEvalChain,
            LabeledCriteriaEvalChain,
        )
        from langchain_classic.evaluation.qa.eval_chain import ContextQAEvalChain

        if "correctness" in selected_metrics:
            evaluators["answer_correctness"] = LabeledCriteriaEvalChain.from_llm(
                llm=llm,
                criteria="correctness",
            )
        if "relevance" in selected_metrics:
            evaluators["answer_relevance"] = CriteriaEvalChain.from_llm(
                llm=llm,
                criteria={
                    "relevance": "Does the answer directly address the user's question and stay on topic?"
                },
            )
        if "groundedness" in selected_metrics:
            evaluators["groundedness"] = ContextQAEvalChain.from_llm(llm=llm)

    if embeddings is not None:
        evaluators["embeddings"] = embeddings

    return evaluators


def cosine_similarity(left: list[float], right: list[float]) -> float | None:
    if not left or not right or len(left) != len(right):
        return None
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return None
    dot_product = sum(left_value * right_value for left_value, right_value in zip(left, right, strict=True))
    similarity = dot_product / (left_norm * right_norm)
    return round(similarity, 6)


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def ensure_output_dir(path: str) -> Path:
    output_dir = Path(path)
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


async def ingest_benchmark_contexts(
    client: BackendRagClient,
    config: EvalRunConfig,
    samples: list[BenchmarkSample],
) -> dict[str, int]:
    ingest_items = build_ingest_items(samples)
    totals = {
        "unique_contexts": len(ingest_items),
        "documents_inserted": 0,
        "chunks_inserted": 0,
    }

    for batch_index, batch in enumerate(chunked(ingest_items, config.batch_size), start=1):
        ingest_payload = await client.ingest_text_items(
            items=batch,
            embedding_provider=config.embedding_provider,
            embedding_model=config.embedding_model,
            force_reingest=config.force_reingest,
        )
        totals["documents_inserted"] += int(ingest_payload.get("documents_inserted", 0) or 0)
        totals["chunks_inserted"] += int(ingest_payload.get("chunks_inserted", 0) or 0)
        print(
            f"Ingested batch {batch_index}: "
            f"{ingest_payload.get('documents_inserted', 0)} docs, "
            f"{ingest_payload.get('chunks_inserted', 0)} chunks"
        )

    return totals


async def query_backend_for_sample(
    client: BackendRagClient,
    config: EvalRunConfig,
    sample: BenchmarkSample,
) -> dict[str, Any]:
    chat_payload = await client.chat(
        message=sample.question,
        top_k=config.top_k,
        generation_provider=config.generation_provider,
        generation_model=config.generation_model,
        embedding_provider=config.embedding_provider,
        embedding_model=config.embedding_model,
    )
    result_row = {
        "sample_id": sample.sample_id,
        "question": sample.question,
        "ground_truth": sample.ground_truth,
        "reference_contexts": sample.reference_contexts,
        "response": normalize_text(chat_payload.get("answer")),
        "retrieved_contexts": extract_retrieved_contexts(chat_payload),
        "citations": chat_payload.get("citations", []),
        "used_fallback": bool(chat_payload.get("used_fallback", False)),
        "provider": chat_payload.get("provider"),
        "model": chat_payload.get("model"),
        "metadata": sample.metadata,
    }
    print(
        f"Evaluated {sample.sample_id}: "
        f"fallback={result_row['used_fallback']} "
        f"retrieved_contexts={len(result_row['retrieved_contexts'])}"
    )
    return result_row


async def run_backend_flow(
    config: EvalRunConfig,
    samples: list[BenchmarkSample],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    results: list[dict[str, Any]] = []

    async with BackendRagClient(
        base_url=config.base_url,
        username=config.username,
        password=config.password,
    ) as client:
        await client.login()
        health_payload = await client.health()
        model_selection = await client.get_model_selection()

        if config.reset_first:
            reset_payload = await client.reset()
            print(f"Reset complete: {json.dumps(reset_payload)}")

        ingest_stats = await ingest_benchmark_contexts(client, config, samples)

        for sample in samples:
            results.append(await query_backend_for_sample(client, config, sample))

    ingest_stats["backend_assumptions"] = health_payload.get("assumptions", {})
    ingest_stats["model_selection"] = model_selection
    return results, ingest_stats


def evaluate_with_langchain(
    config: EvalRunConfig,
    rows: list[dict[str, Any]],
) -> tuple[dict[str, float], list[dict[str, Any]]]:
    evaluators = build_langchain_evaluators(config)
    limiter = JudgeRateLimiter(config.judge_max_rpm)
    aggregate_fields = [
        "langchain_answer_correctness",
        "langchain_answer_relevance",
        "langchain_groundedness",
        "langchain_embedding_similarity",
    ]

    if evaluators:
        print(
            "Starting LangChain scoring: "
            f"{len(rows)} samples, evaluators={', '.join(sorted(evaluators.keys()))}, "
            f"judge_max_rpm={config.judge_max_rpm}"
        )
    else:
        print("Skipping LangChain judge scoring: no judge evaluators are configured or reachable.")

    per_sample_rows: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        scored_row = dict(row)

        if "answer_correctness" in evaluators:
            try:
                limiter.wait(
                    sample_id=row["sample_id"],
                    sample_index=index,
                    total_samples=len(rows),
                    metric_name="answer_correctness",
                )
                payload = evaluators["answer_correctness"].evaluate_strings(
                    prediction=row["response"],
                    input=row["question"],
                    reference=row["ground_truth"],
                )
                scored_row["langchain_answer_correctness"] = parse_evaluator_score(payload)
                scored_row["langchain_answer_correctness_label"] = extract_evaluator_label(payload)
            except Exception as exc:
                scored_row["langchain_answer_correctness"] = None
                scored_row["langchain_answer_correctness_error"] = str(exc)

        if "answer_relevance" in evaluators:
            try:
                limiter.wait(
                    sample_id=row["sample_id"],
                    sample_index=index,
                    total_samples=len(rows),
                    metric_name="answer_relevance",
                )
                payload = evaluators["answer_relevance"].evaluate_strings(
                    prediction=row["response"],
                    input=row["question"],
                )
                scored_row["langchain_answer_relevance"] = parse_evaluator_score(payload)
                scored_row["langchain_answer_relevance_label"] = extract_evaluator_label(payload)
            except Exception as exc:
                scored_row["langchain_answer_relevance"] = None
                scored_row["langchain_answer_relevance_error"] = str(exc)

        if "groundedness" in evaluators:
            joined_context = "\n\n".join(row["retrieved_contexts"])
            if joined_context:
                try:
                    limiter.wait(
                        sample_id=row["sample_id"],
                        sample_index=index,
                        total_samples=len(rows),
                        metric_name="groundedness",
                    )
                    payload = evaluators["groundedness"].evaluate_strings(
                        prediction=row["response"],
                        input=row["question"],
                        reference=joined_context,
                    )
                    scored_row["langchain_groundedness"] = parse_evaluator_score(payload)
                    scored_row["langchain_groundedness_label"] = extract_evaluator_label(payload)
                except Exception as exc:
                    scored_row["langchain_groundedness"] = None
                    scored_row["langchain_groundedness_error"] = str(exc)
            else:
                scored_row["langchain_groundedness"] = None
                scored_row["langchain_groundedness_label"] = None

        if "embeddings" in evaluators:
            try:
                limiter.wait(
                    sample_id=row["sample_id"],
                    sample_index=index,
                    total_samples=len(rows),
                    metric_name="embedding_prediction",
                )
                prediction_embedding = evaluators["embeddings"].embed_query(row["response"])
                limiter.wait(
                    sample_id=row["sample_id"],
                    sample_index=index,
                    total_samples=len(rows),
                    metric_name="embedding_reference",
                )
                reference_embedding = evaluators["embeddings"].embed_query(row["ground_truth"])
                scored_row["langchain_embedding_similarity"] = cosine_similarity(
                    prediction_embedding,
                    reference_embedding,
                )
            except Exception as exc:
                scored_row["langchain_embedding_similarity"] = None
                scored_row["langchain_embedding_similarity_error"] = str(exc)

        per_sample_rows.append(scored_row)
        print(f"Scored {row['sample_id']} ({index}/{len(rows)})")

    langchain_means: dict[str, float] = {}
    for field_name in aggregate_fields:
        values = [
            float(row[field_name])
            for row in per_sample_rows
            if isinstance(row.get(field_name), (int, float))
        ]
        if values:
            langchain_means[field_name] = round(sum(values) / len(values), 6)

    return langchain_means, per_sample_rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return

    fieldnames: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            serialized = {
                key: json.dumps(value, ensure_ascii=False)
                if isinstance(value, (dict, list))
                else value
                for key, value in row.items()
            }
            writer.writerow(serialized)


def safe_mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(mean(values), 6)


def to_score(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    return None


def text_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return round(fuzz.token_set_ratio(left, right) / 100.0, 6)


def normalize_eval_text(value: str) -> str:
    lowered = normalize_text(value).lower()
    lowered = re.sub(r"\b(a|an|the)\b", " ", lowered)
    lowered = re.sub(r"[^a-z0-9\s]", " ", lowered)
    return " ".join(lowered.split())


def token_f1_components(prediction: str, reference: str) -> tuple[float, float, float]:
    prediction_tokens = normalize_eval_text(prediction).split()
    reference_tokens = normalize_eval_text(reference).split()
    if not prediction_tokens and not reference_tokens:
        return 1.0, 1.0, 1.0
    if not prediction_tokens or not reference_tokens:
        return 0.0, 0.0, 0.0

    overlap_counts: dict[str, int] = {}
    for token in reference_tokens:
        overlap_counts[token] = overlap_counts.get(token, 0) + 1

    overlap = 0
    for token in prediction_tokens:
        available = overlap_counts.get(token, 0)
        if available > 0:
            overlap += 1
            overlap_counts[token] = available - 1

    if overlap == 0:
        return 0.0, 0.0, 0.0

    precision = overlap / len(prediction_tokens)
    recall = overlap / len(reference_tokens)
    f1 = (2 * precision * recall) / (precision + recall)
    return round(precision, 6), round(recall, 6), round(f1, 6)


def exact_match_score(prediction: str, reference: str) -> float:
    return 1.0 if normalize_eval_text(prediction) == normalize_eval_text(reference) else 0.0


def lexical_context_matches(
    reference_contexts: list[str],
    retrieved_contexts: list[str],
    threshold: float = DEFAULT_CONTEXT_MATCH_THRESHOLD,
) -> tuple[int, int]:
    matched_references = 0
    for reference in reference_contexts:
        if any(text_similarity(reference, retrieved) >= threshold for retrieved in retrieved_contexts):
            matched_references += 1

    matched_retrieved = 0
    for retrieved in retrieved_contexts:
        if any(text_similarity(reference, retrieved) >= threshold for reference in reference_contexts):
            matched_retrieved += 1

    return matched_references, matched_retrieved


def best_context_match(reference_contexts: list[str], retrieved_contexts: list[str]) -> float:
    best = 0.0
    for reference in reference_contexts:
        for retrieved in retrieved_contexts:
            best = max(best, text_similarity(reference, retrieved))
    return round(best, 6)


def count_scores_at_or_above(values: list[float], threshold: float) -> int:
    return sum(1 for value in values if value >= threshold)


def build_diagnostic_row(row: dict[str, Any], quality_score: float | None) -> dict[str, Any]:
    return {
        "sample_id": row["sample_id"],
        "question": row["question"],
        "quality_score": quality_score,
        "used_fallback": row["used_fallback"],
        "retrieved_contexts": len(row.get("retrieved_contexts", [])),
        "response_preview": normalize_text(row.get("response"))[:200],
        "ground_truth_preview": normalize_text(row.get("ground_truth"))[:200],
    }


def build_summary(
    config: EvalRunConfig,
    rows: list[dict[str, Any]],
    langchain_means: dict[str, float],
    ingest_stats: dict[str, int],
) -> dict[str, Any]:
    exact_match_scores = [
        exact_match_score(row["response"], row["ground_truth"])
        for row in rows
    ]
    token_precision_scores: list[float] = []
    token_recall_scores: list[float] = []
    token_f1_scores: list[float] = []
    answer_similarity_scores = [
        text_similarity(row["response"], row["ground_truth"])
        for row in rows
    ]
    context_match_scores = [
        best_context_match(row["reference_contexts"], row["retrieved_contexts"])
        for row in rows
    ]
    retrieval_reference_recall_scores: list[float] = []
    retrieval_precision_scores: list[float] = []
    retrieved_counts = [len(row["retrieved_contexts"]) for row in rows]
    citation_counts = [len(row.get("citations", [])) for row in rows]
    response_lengths = [len(normalize_text(row["response"])) for row in rows]
    answered_count = sum(1 for row in rows if normalize_text(row["response"]))
    fallback_count = sum(1 for row in rows if row["used_fallback"])
    retrieved_count = sum(1 for row in rows if row["retrieved_contexts"])

    for row, answer_similarity, context_match in zip(
        rows, answer_similarity_scores, context_match_scores, strict=True
    ):
        token_precision, token_recall, token_f1 = token_f1_components(row["response"], row["ground_truth"])
        matched_references, matched_retrieved = lexical_context_matches(
            row["reference_contexts"],
            row["retrieved_contexts"],
        )
        reference_count = len(row["reference_contexts"])
        retrieved_count_for_row = len(row["retrieved_contexts"])

        token_precision_scores.append(token_precision)
        token_recall_scores.append(token_recall)
        token_f1_scores.append(token_f1)
        retrieval_reference_recall_scores.append(
            round(matched_references / reference_count, 6) if reference_count else 0.0
        )
        retrieval_precision_scores.append(
            round(matched_retrieved / retrieved_count_for_row, 6) if retrieved_count_for_row else 0.0
        )

        row["answer_exact_match"] = exact_match_score(row["response"], row["ground_truth"])
        row["answer_token_precision"] = token_precision
        row["answer_token_recall"] = token_recall
        row["answer_token_f1"] = token_f1
        row["answer_similarity"] = answer_similarity
        row["retrieval_context_match"] = context_match
        row["retrieval_reference_recall"] = retrieval_reference_recall_scores[-1]
        row["retrieval_context_precision"] = retrieval_precision_scores[-1]

    available_quality_metrics = [
        metric_name
        for metric_name in [
            "langchain_answer_correctness",
            "langchain_answer_relevance",
            "langchain_groundedness",
            "langchain_embedding_similarity",
        ]
        if metric_name in langchain_means
    ]
    quality_values: list[float] = []
    for row in rows:
        numeric_components = [
            score
            for score in (to_score(row.get(metric_name)) for metric_name in available_quality_metrics)
            if score is not None
        ]
        if numeric_components:
            row["quality_score"] = round(sum(numeric_components) / len(numeric_components), 6)
            quality_values.append(row["quality_score"])
        else:
            row["quality_score"] = None

    weakest_samples = [
        build_diagnostic_row(row, row["quality_score"])
        for row in sorted(
            rows,
            key=lambda item: (
                1 if item.get("quality_score") is None else 0,
                item.get("quality_score") if item.get("quality_score") is not None else 999.0,
                item["answer_similarity"],
            ),
        )[:5]
    ]

    return {
        "run": {
            "num_samples": len(rows),
            "sample_size": len(rows),
            "requested_sample_size": config.sample_size,
            "dataset_source": config.dataset_source,
            "judge_llm_model": config.eval_llm_model,
            "judge_embedding_model": config.eval_embedding_model,
            "judge_metrics": config.judge_metrics,
            "judge_max_rpm": config.judge_max_rpm,
            "evaluator": "langchain",
        },
        "operational": {
            "answered_rate": round(answered_count / len(rows), 6),
            "fallback_rate": round(fallback_count / len(rows), 6),
            "retrieval_hit_rate": round(retrieved_count / len(rows), 6),
            "avg_retrieved_contexts": safe_mean([float(value) for value in retrieved_counts]),
            "avg_citations": safe_mean([float(value) for value in citation_counts]),
            "avg_response_chars": safe_mean([float(value) for value in response_lengths]),
            "unique_contexts_ingested": ingest_stats["unique_contexts"],
            "documents_inserted": ingest_stats["documents_inserted"],
            "chunks_inserted": ingest_stats["chunks_inserted"],
        },
        "backend_selection": {
            "active": ingest_stats.get("model_selection"),
        },
        "backend_reranker": {
            "enabled": ingest_stats.get("backend_assumptions", {}).get("rerank_enabled"),
            "model": ingest_stats.get("backend_assumptions", {}).get("rerank_model"),
            "invoke_url": ingest_stats.get("backend_assumptions", {}).get("rerank_invoke_url"),
            "max_candidates": ingest_stats.get("backend_assumptions", {}).get("rerank_max_candidates"),
            "min_candidates": ingest_stats.get("backend_assumptions", {}).get("rerank_min_candidates"),
        },
        "retrieval": {
            "avg_context_match": safe_mean(context_match_scores),
            "context_match_rate_at_0_85": round(
                count_scores_at_or_above(context_match_scores, DEFAULT_CONTEXT_MATCH_THRESHOLD) / len(rows),
                6,
            ),
            "avg_reference_recall_at_k_lexical": safe_mean(retrieval_reference_recall_scores),
            "avg_context_precision_at_k_lexical": safe_mean(retrieval_precision_scores),
            "reference_recall_at_k_hit_rate": round(
                count_scores_at_or_above(retrieval_reference_recall_scores, 1.0) / len(rows),
                6,
            ),
        },
        "generation": {
            "langchain_answer_correctness": langchain_means.get("langchain_answer_correctness"),
            "langchain_answer_relevance": langchain_means.get("langchain_answer_relevance"),
            "langchain_groundedness": langchain_means.get("langchain_groundedness"),
            "langchain_embedding_similarity": langchain_means.get("langchain_embedding_similarity"),
            "avg_exact_match": safe_mean(exact_match_scores),
            "avg_token_precision": safe_mean(token_precision_scores),
            "avg_token_recall": safe_mean(token_recall_scores),
            "avg_token_f1": safe_mean(token_f1_scores),
            "exact_match_rate": round(sum(exact_match_scores) / len(rows), 6),
            "avg_answer_similarity": safe_mean(answer_similarity_scores),
            "answer_match_rate_at_0_70": round(
                count_scores_at_or_above(answer_similarity_scores, DEFAULT_ANSWER_MATCH_THRESHOLD) / len(rows),
                6,
            ),
            "avg_quality_score": safe_mean(quality_values),
            "quality_pass_rate_at_0_70": (
                round(count_scores_at_or_above(quality_values, 0.70) / len(quality_values), 6)
                if quality_values
                else None
            ),
        },
        "raw_langchain_metrics": langchain_means,
        "weakest_samples": weakest_samples,
    }


def build_config_snapshot(config: EvalRunConfig, ingest_stats: dict[str, Any]) -> dict[str, Any]:
    return {
        "base_url": config.base_url,
        "username": config.username,
        "requested_sample_size": config.sample_size,
        "dataset_source": config.dataset_source,
        "dataset_path": config.dataset_path,
        "hf_dataset": config.hf_dataset,
        "hf_config": config.hf_config,
        "hf_split": config.hf_split,
        "sample_size": config.sample_size,
        "top_k": config.top_k,
        "batch_size": config.batch_size,
        "reset_first": config.reset_first,
        "force_reingest": config.force_reingest,
        "embedding_provider": config.embedding_provider,
        "embedding_model": config.embedding_model,
        "generation_provider": config.generation_provider,
        "generation_model": config.generation_model,
        "eval_llm_model": config.eval_llm_model,
        "eval_llm_base_url": config.eval_llm_base_url,
        "eval_embedding_model": config.eval_embedding_model,
        "eval_embedding_base_url": config.eval_embedding_base_url,
        "judge_metrics": config.judge_metrics,
        "judge_max_rpm": config.judge_max_rpm,
        "evaluator": "langchain",
        "backend_selection": ingest_stats.get("model_selection"),
        "backend_reranker": {
            "enabled": ingest_stats.get("backend_assumptions", {}).get("rerank_enabled"),
            "model": ingest_stats.get("backend_assumptions", {}).get("rerank_model"),
            "invoke_url": ingest_stats.get("backend_assumptions", {}).get("rerank_invoke_url"),
            "max_candidates": ingest_stats.get("backend_assumptions", {}).get("rerank_max_candidates"),
            "min_candidates": ingest_stats.get("backend_assumptions", {}).get("rerank_min_candidates"),
        },
    }


async def async_main() -> None:
    config = build_config(parse_args())
    output_dir = ensure_output_dir(config.output_dir)

    samples = load_samples(config)
    print(f"Loaded sample size: {len(samples)} (requested sample size: {config.sample_size})")

    if not samples:
        raise RuntimeError("No benchmark samples were loaded.")

    backend_rows, ingest_stats = await run_backend_flow(config, samples)
    langchain_means, per_sample_rows = evaluate_with_langchain(config, backend_rows)
    summary = build_summary(config, per_sample_rows, langchain_means, ingest_stats)

    write_json(output_dir / "config.json", build_config_snapshot(config, ingest_stats))
    write_json(output_dir / "summary.json", summary)
    write_jsonl(output_dir / "backend_results.jsonl", backend_rows)
    write_jsonl(output_dir / "langchain_scored_results.jsonl", per_sample_rows)
    write_csv(output_dir / "langchain_scored_results.csv", per_sample_rows)

    print("\nEvaluation summary")
    print(json.dumps(summary, indent=2))
    print(f"\nArtifacts written to: {output_dir}")


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
