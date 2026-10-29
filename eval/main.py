from deepeval.test_case import LLMTestCase, LLMTestCaseParams
from deepeval.metrics import (
    AnswerRelevancyMetric,
    ContextualPrecisionMetric,
    ContextualRecallMetric,
    ContextualRelevancyMetric,
    FaithfulnessMetric,
    GEval,
)
from openai import AsyncOpenAI
import os
import math
import re
from client_backend import BackendRagClient
from tqdm.auto import tqdm
import pandas as pd
from pathlib import Path
import json
import ast
import asyncio
import datetime

BASE_URL = "http://localhost:9010"
USERNAME = "admin"
PASSWORD = "change-me-immediately"
DATASET_PATH = Path("eval/dataset/testset.csv").resolve()
DATASET_MANIFEST_PATH = Path("eval/dataset/testset.manifest.json").resolve()
INGEST_FILE_PATHS: list[str] = []
BACKEND_TIMEOUT_SECONDS = 600.0
RUNS_ROOT = Path("eval/output").resolve()
RUN_NAME = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
RUN_DIR = (RUNS_ROOT / RUN_NAME).resolve()
RAW_RESULTS_PATH = RUN_DIR / "chat_results.jsonl"
METRIC_RESULTS_PATH = RUN_DIR / "metric_results.csv"
SUMMARY_PATH = RUN_DIR / "summary.json"
INGEST_STATE_PATH = RUN_DIR / "ingest_state.json"
RUN_CONFIG_PATH = RUN_DIR / "run_config.json"

RESET_BACKEND_BEFORE_INGEST = False
FORCE_REINGEST = False
TOP_K = 5
ENABLE_RATE_LIMIT_DELAY = False
REQUEST_DELAY_SECONDS = 0.1
ALLOWED_SYNTHESIZER_NAMES: list[str] = []
SYSTEM_PROMPT_OVERRIDE = ""
FOCUSED_METRIC_COLUMNS = {"contextual_relevancy", "hit_rate", "mrr"}

EVAL_GENERATION_PROFILE = "nim_35super120"
EVAL_EMBEDDING_PROFILE = "nim_nemotron_2048"

CRITIC_MODEL = "gpt-5-mini"
FORCE_RECOMPUTE_METRIC_COLUMNS = {"contextual_relevancy", "hit_rate", "mrr"}
RETRIEVAL_OVERLAP_THRESHOLD = 0.18
SUMMARY_METRIC_COLUMNS = [
    "answer_correctness",
    "answer_relevancy",
    "faithfulness",
    "contextual_precision",
    "contextual_recall",
    "contextual_relevancy",
    "hit_rate",
    "mrr",
]

# ---------------------------------------------------------------------------
# DeepEval metric definitions
# ---------------------------------------------------------------------------
# Each metric is constructed once and reused across all test cases.
# `model=` accepts a plain model-name string; DeepEval wraps it internally.
# `async_mode=True` lets DeepEval use its own async batching per metric.
# threshold= is the pass/fail boundary — does not affect the numeric score.

DEEPEVAL_METRICS: dict[str, object] = {
    "answer_relevancy": AnswerRelevancyMetric(
        model=CRITIC_MODEL,
        threshold=0.7,
        async_mode=True,
    ),
    "faithfulness": FaithfulnessMetric(
        model=CRITIC_MODEL,
        threshold=0.7,
        async_mode=True,
    ),
    "contextual_precision": ContextualPrecisionMetric(
        model=CRITIC_MODEL,
        threshold=0.7,
        async_mode=True,
    ),
    "contextual_recall": ContextualRecallMetric(
        model=CRITIC_MODEL,
        threshold=0.7,
        async_mode=True,
    ),
    "contextual_relevancy": ContextualRelevancyMetric(
        model=CRITIC_MODEL,
        threshold=0.7,
        async_mode=True,
    ),
    
    # GEval — G-Eval with logprob-weighted integer scoring (Liu et al., 2023). 
    # Separate from the other metrics so the reference answer is included only
    # here (correctness needs ground-truth; relevancy must NOT see it).
    "answer_correctness": GEval(
        name="answer_correctness",
        criteria=(
            "Determine whether the actual_output is factually correct and "
            "complete relative to the expected_output (reference answer). "
            "Penalise hallucinated facts, missing key information, and "
            "numerical errors."
        ),
        evaluation_params=[
            LLMTestCaseParams.INPUT,
            LLMTestCaseParams.ACTUAL_OUTPUT,
            LLMTestCaseParams.EXPECTED_OUTPUT,
        ],
        model=CRITIC_MODEL,
        threshold=0.5,
        async_mode=True,
    ),
}

# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------


def ensure_run_dir() -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)


def _openai_client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])


# ---------------------------------------------------------------------------
# Dataset loading
# ---------------------------------------------------------------------------

def load_samples() -> list[dict]:
    df = pd.read_csv(DATASET_PATH)
    df = df.fillna("")
    if "synthesizer_name" in df.columns and ALLOWED_SYNTHESIZER_NAMES:
        df = df[df["synthesizer_name"].isin(
            ALLOWED_SYNTHESIZER_NAMES)].reset_index(drop=True)
    samples = []
    for index, row in df.iterrows():
        sample_id = str(row.get("sample_id") or row.get("id") or index)
        reference_contexts = parse_reference_contexts(
            row.get("reference_contexts", ""))
        samples.append(
            {
                "sample_id": sample_id,
                "user_input": str(row["user_input"]),
                "reference": str(row["reference"]),
                "reference_contexts": reference_contexts,
                "synthesizer_name": str(row.get("synthesizer_name", "")),
            }
        )
    return samples


def load_dataset_df() -> pd.DataFrame:
    return pd.read_csv(DATASET_PATH).fillna("")


def dataset_has_metric_columns(df: pd.DataFrame) -> bool:
    return any(col in df.columns for col in SUMMARY_METRIC_COLUMNS)


def parse_reference_contexts(value) -> list[str]:
    def flatten(item) -> list[str]:
        if item is None:
            return []
        if isinstance(item, list):
            output = []
            for child in item:
                output.extend(flatten(child))
            return output
        text = str(item).strip()
        return [text] if text else []

    if isinstance(value, list):
        return flatten(value)

    text = str(value).strip()
    if not text:
        return []

    for parser in (json.loads, ast.literal_eval):
        try:
            loaded = parser(text)
            flattened = flatten(loaded)
            if flattened:
                reparsed = []
                for item in flattened:
                    nested = item.strip()
                    if (nested.startswith("[") and nested.endswith("]")) or (
                        nested.startswith("{") and nested.endswith("}")
                    ):
                        try:
                            nested_loaded = ast.literal_eval(nested)
                            reparsed.extend(flatten(nested_loaded))
                            continue
                        except (ValueError, SyntaxError):
                            pass
                    reparsed.append(item)
                return reparsed
        except (json.JSONDecodeError, ValueError, SyntaxError):
            continue

    return [text]


_EVAL_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
_EVAL_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how",
    "in", "is", "it", "of", "on", "or", "that", "the", "this", "to", "what",
    "when", "where", "which", "who", "with",
}
_NOISY_CONTEXT_PREFIXES = (
    "title:",
    "workbook:",
    "sheet:",
    "row:",
    "s/n:",
    "remarks",
    "column_",
    "link provided:",
)


def normalize_context_text(text: str) -> str:
    cleaned_lines: list[str] = []
    for raw_line in str(text).splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        lowered = stripped.lower()
        if any(lowered.startswith(prefix) for prefix in _NOISY_CONTEXT_PREFIXES):
            continue
        if lowered.startswith("revised answer"):
            continue
        cleaned_lines.append(stripped)
    return " ".join(" ".join(cleaned_lines).split())


def tokenize_for_overlap(text: str) -> set[str]:
    normalized = normalize_context_text(text).lower()
    return {
        token
        for token in _EVAL_TOKEN_PATTERN.findall(normalized)
        if len(token) >= 3 and token not in _EVAL_STOPWORDS
    }


def max_reference_overlap(candidate_chunk: str, reference_contexts: list[str]) -> float:
    candidate_tokens = tokenize_for_overlap(candidate_chunk)
    if not candidate_tokens:
        return 0.0

    best = 0.0
    for reference_context in reference_contexts:
        reference_tokens = tokenize_for_overlap(reference_context)
        if not reference_tokens:
            continue
        overlap = len(candidate_tokens & reference_tokens) / max(
            1,
            min(len(candidate_tokens), len(reference_tokens)),
        )
        best = max(best, overlap)
    return best


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def load_existing_chat_results() -> dict[str, dict]:
    if not RAW_RESULTS_PATH.exists():
        return {}
    results = {}
    with RAW_RESULTS_PATH.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            results[str(item["sample_id"])] = item
    return results


def append_chat_result(item: dict) -> None:
    with RAW_RESULTS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, ensure_ascii=False) + "\n")


def save_chat_results(rows: list[dict]) -> None:
    with RAW_RESULTS_PATH.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def save_json(path: Path, payload: dict) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_metric_results() -> pd.DataFrame:
    if METRIC_RESULTS_PATH.exists():
        return pd.read_csv(METRIC_RESULTS_PATH).fillna("")
    return pd.DataFrame()


def save_metric_results(df: pd.DataFrame) -> None:
    df.to_csv(METRIC_RESULTS_PATH, index=False, encoding="utf-8-sig")


def extract_retrieved_contexts(chat_payload: dict) -> list[str]:
    if "retrieved_chunks" not in chat_payload:
        # Only the debug response shape carries retrieved chunks; without them
        # every context metric would silently score zero.
        raise RuntimeError(
            "The backend returned the public chat response without retrieved_chunks. "
            "Run the evaluation against a backend with CHAT_DEBUG_ENABLED=true."
        )
    contexts = []
    for chunk in chat_payload.get("retrieved_chunks", []):
        content = str(chunk.get("content", "")).strip()
        if content:
            contexts.append(content)
            continue
        snippet = str(chunk.get("snippet", "")).strip()
        if snippet:
            contexts.append(snippet)
    return contexts


def resolve_ingest_files() -> list[Path]:
    if INGEST_FILE_PATHS:
        return [Path(raw_path).resolve() for raw_path in INGEST_FILE_PATHS]

    if DATASET_MANIFEST_PATH.exists():
        manifest = load_json(DATASET_MANIFEST_PATH)
        manifest_files = manifest.get("files", [])
        if isinstance(manifest_files, list) and manifest_files:
            return [Path(str(raw_path)).resolve() for raw_path in manifest_files]

    source_files = pd.read_csv(DATASET_PATH).fillna("").get("source_file")
    if source_files is None:
        return []

    resolved: list[Path] = []
    for value in source_files.tolist():
        candidate = Path(str(value).strip())
        if candidate.is_file():
            resolved.append(candidate.resolve())
    deduped = {path: None for path in resolved}
    return sorted(deduped)


def build_run_config(
    *,
    dataset_path: Path,
    ingest_files: list[Path],
    model_selection: dict,
    system_prompt_payload: dict,
) -> dict:
    return {
        "dataset_path": str(dataset_path),
        "dataset_manifest_path": str(DATASET_MANIFEST_PATH) if DATASET_MANIFEST_PATH.exists() else "",
        "dataset_manifest": load_json(DATASET_MANIFEST_PATH) if DATASET_MANIFEST_PATH.exists() else None,
        "allowed_synthesizer_names": ALLOWED_SYNTHESIZER_NAMES,
        "ingest_files": [str(path) for path in ingest_files],
        "reset_backend_before_ingest": RESET_BACKEND_BEFORE_INGEST,
        "force_reingest": FORCE_REINGEST,
        "top_k": TOP_K,
        "eval_generation_profile": EVAL_GENERATION_PROFILE,
        "eval_embedding_profile": EVAL_EMBEDDING_PROFILE,
        "critic_model": CRITIC_MODEL,
        "active_model_selection": model_selection,
        "active_system_prompt": system_prompt_payload,
        "system_prompt_override_applied": bool(SYSTEM_PROMPT_OVERRIDE.strip()),
    }


# ---------------------------------------------------------------------------
# row -> LLMTestCase
# ---------------------------------------------------------------------------

def row_to_test_case(row: dict, *, normalize_contexts: bool = False) -> LLMTestCase:
    """Convert a chat-result row into a DeepEval LLMTestCase.

    Field mapping
    -------------
    input             <- user_input           (the question asked)
    actual_output     <- response             (what the RAG backend returned)
    expected_output   <- reference            (ground-truth answer from testset)
    retrieval_context <- retrieved_contexts   (what the retriever actually fetched)
    context           <- reference_contexts   (ground-truth supporting passages)
    """
    retrieval_context = row.get("retrieved_contexts", [])
    context = row.get("reference_contexts", [])
    if normalize_contexts:
        retrieval_context = [
            cleaned for item in retrieval_context if (cleaned := normalize_context_text(item))
        ]
        context = [
            cleaned for item in context if (cleaned := normalize_context_text(item))
        ]

    return LLMTestCase(
        input=row["user_input"],
        actual_output=row["response"],
        expected_output=row.get("reference", ""),
        retrieval_context=retrieval_context,
        context=context,
    )


# ---------------------------------------------------------------------------
# DeepEval metric evaluation — with column-existence skip
# ---------------------------------------------------------------------------

async def evaluate_deepeval_metric_if_needed(
    *,
    col_name: str,
    metric,
    rows: list[dict],
    base_df: pd.DataFrame,
) -> pd.DataFrame:
    """Compute a single DeepEval metric across all rows if not already present.

    Skip logic
    ----------
    If ``col_name`` already exists as a column in ``base_df`` (i.e. the CSV
    was produced by a previous run or a prior step in this run), the metric
    is skipped entirely and the dataframe is returned unchanged.  This means
    a crashed run can be resumed without re-spending API budget on already-
    scored rows.

    Incremental save
    ----------------
    After each row is scored the partial results are flushed to disk so that
    a mid-run interruption loses at most one row of work.
    """
    force_recompute = col_name in FORCE_RECOMPUTE_METRIC_COLUMNS
    if col_name in base_df.columns and not force_recompute:
        existing_scores = pd.to_numeric(base_df[col_name], errors="coerce")
        if existing_scores.notna().all():
            print(f"  {col_name}: column already complete — skipping.")
            return base_df
        print(f"  {col_name}: partial column found — recomputing missing rows.")

    if col_name in base_df.columns and force_recompute:
        print(f"  {col_name}: forced recompute enabled.")

    print(f"  Computing {col_name} ...")
    scores: list[float | None] = []

    for row in tqdm(rows, desc=col_name, unit="sample"):
        test_case = row_to_test_case(
            row,
            normalize_contexts=(col_name == "contextual_relevancy"),
        )
        try:
            # DeepEval's internal Rich indicator renders poorly in some IDE
            # consoles and appears as a static bar. Disable it and let the
            # outer tqdm loop show real per-sample progress instead.
            await metric.a_measure(test_case, _show_indicator=False)
            scores.append(float(metric.score))
        except asyncio.CancelledError as exc:
            print(
                f"    [WARN] {col_name} cancelled for sample {row.get('sample_id')}: {exc}")
            scores.append(None)
        except Exception as exc:
            print(
                f"    [WARN] {col_name} failed for sample {row.get('sample_id')}: {exc}")
            scores.append(None)

        # Incremental save
        partial = base_df.copy()
        n = len(scores)
        partial[col_name] = scores + [None] * (len(base_df) - n)
        save_metric_results(partial)

        if ENABLE_RATE_LIMIT_DELAY:
            await asyncio.sleep(REQUEST_DELAY_SECONDS)

    final = base_df.copy()
    final[col_name] = scores
    save_metric_results(final)
    return final


# ---------------------------------------------------------------------------
# Retrieval metrics — hit_rate + MRR  (ARES chunk-judge)
# ---------------------------------------------------------------------------
# DeepEval does not ship hit_rate / MRR directly so we keep the
# LLM-as-a-judge approach from ARES (Saad-Falcon et al., 2023) using the
# OpenAI async client for logprob-based verdict scoring.

async def _judge_chunk_relevant(
    client: AsyncOpenAI,
    *,
    question: str,
    reference: str,
    reference_contexts: list[str],
    candidate_chunk: str,
) -> bool:
    """Return True if the candidate chunk is relevant enough to answer the question."""
    overlap_score = max_reference_overlap(candidate_chunk, reference_contexts)
    if overlap_score >= RETRIEVAL_OVERLAP_THRESHOLD:
        return True

    normalized_reference_contexts = [
        cleaned for item in reference_contexts if (cleaned := normalize_context_text(item))
    ]
    normalized_candidate_chunk = normalize_context_text(candidate_chunk)
    ref_ctx = "\n\n".join(
        normalized_reference_contexts[:3]) if normalized_reference_contexts else "None"
    prompt = (
        f"Decide whether the candidate retrieved chunk is relevant enough to "
        f"support answering the question correctly.\n\n"
        f"Question:\n{question}\n\n"
        f"Reference Answer:\n{reference}\n\n"
        f"Reference Context:\n{ref_ctx}\n\n"
        f"Candidate Chunk:\n{normalized_candidate_chunk}\n\n"
        f"Reply with exactly one word: YES or NO"
    )

    supports_legacy_temperature_override = not CRITIC_MODEL.startswith("gpt-5")
    base_request = {
        "model": CRITIC_MODEL,
        "messages": [{"role": "user", "content": prompt}],
    }
    if supports_legacy_temperature_override:
        base_request["temperature"] = 0

    try:
        resp = await client.chat.completions.create(
            **base_request,
            max_completion_tokens=1,
            logprobs=True,
            top_logprobs=5,
        )
        top = resp.choices[0].logprobs.content[0].top_logprobs
        yes_prob = sum(math.exp(e.logprob)
                       for e in top if e.token.strip().upper() == "YES")
        no_prob = sum(math.exp(e.logprob)
                      for e in top if e.token.strip().upper() == "NO")
        if yes_prob + no_prob == 0:
            return resp.choices[0].message.content.strip().upper() == "YES"
        return yes_prob >= no_prob
    except Exception as exc:
        error_text = str(exc).lower()
        unsupported_fast_path = any(
            token in error_text
            for token in ("logprobs", "temperature", "max_tokens", "unsupported parameter", "unsupported value")
        )
        if not unsupported_fast_path:
            raise
        resp = await client.chat.completions.create(
            **base_request,
            max_completion_tokens=3,
        )
        return resp.choices[0].message.content.strip().upper().startswith("YES")


async def compute_retrieval_metrics_if_needed(
    *,
    rows: list[dict],
    base_df: pd.DataFrame,
) -> tuple[list[dict], pd.DataFrame]:
    """Compute hit_rate and mrr if either column is missing from base_df.

    Both columns are computed together in one pass since they share the same
    per-chunk judge calls.  If both already exist the entire pass is skipped.
    """
    if {"hit_rate", "mrr"}.issubset(base_df.columns) and not (
        {"hit_rate", "mrr"} & FORCE_RECOMPUTE_METRIC_COLUMNS
    ):
        print("  hit_rate / mrr: columns already present — skipping.")
        return rows, base_df

    if {"hit_rate", "mrr"} & FORCE_RECOMPUTE_METRIC_COLUMNS:
        print("  hit_rate / mrr: forced recompute enabled.")
    print("  Computing hit_rate and mrr (ARES chunk-relevance judge)...")
    client = _openai_client()
    enriched: list[dict] = []
    updated_metric_df = base_df.copy()

    for row in tqdm(rows, desc="hit_rate/mrr", unit="sample"):
        rank: int | None = None
        for idx, chunk in enumerate(row.get("retrieved_contexts", [])[:TOP_K], start=1):
            is_relevant = await _judge_chunk_relevant(
                client,
                question=row["user_input"],
                reference=row["reference"],
                reference_contexts=row.get("reference_contexts", []),
                candidate_chunk=chunk,
            )
            if is_relevant:
                rank = idx
                break
            if ENABLE_RATE_LIMIT_DELAY:
                await asyncio.sleep(REQUEST_DELAY_SECONDS)

        updated = dict(row)
        updated["hit_rate"] = 1.0 if rank is not None else 0.0
        updated["mrr"] = (1.0 / rank) if rank is not None else 0.0
        enriched.append(updated)

        # Incremental save so a crash loses at most one row
        save_chat_results(enriched + rows[len(enriched):])
        current_index = len(enriched) - 1
        updated_metric_df.loc[current_index, "hit_rate"] = updated["hit_rate"]
        updated_metric_df.loc[current_index, "mrr"] = updated["mrr"]
        save_metric_results(updated_metric_df)
        if ENABLE_RATE_LIMIT_DELAY:
            await asyncio.sleep(REQUEST_DELAY_SECONDS)

    save_chat_results(enriched)
    return enriched, updated_metric_df


# ---------------------------------------------------------------------------
# Document ingest
# ---------------------------------------------------------------------------

async def ingest_docx_if_needed(client: BackendRagClient) -> None:
    print("[1/4] Checking document ingest state...")
    if RESET_BACKEND_BEFORE_INGEST:
        print("Resetting backend state before ingest...")
        reset_result = await client.reset()
        print(
            "Backend reset complete: "
            f"documents_deleted={reset_result.get('documents_deleted', 0)}, "
            f"chunks_deleted={reset_result.get('chunks_deleted', 0)}, "
            f"redis_keys_deleted={reset_result.get('redis_keys_deleted', 0)}"
        )
        save_json(INGEST_STATE_PATH, {
                  "status": "reset_completed", "result": reset_result})

    if INGEST_STATE_PATH.exists() and not RESET_BACKEND_BEFORE_INGEST:
        print("Ingest already completed. Reusing saved ingest state.")
        return

    ingest_paths = resolve_ingest_files()
    if not ingest_paths:
        print("No ingest files resolved from config or dataset manifest. Skipping ingest step.")
        save_json(INGEST_STATE_PATH, {
                  "status": "skipped", "reason": "no_ingest_source_found", "files": []})
        return

    text_item_paths = [path for path in ingest_paths if path.suffix.lower() in {".md", ".txt"}]
    upload_paths = [path for path in ingest_paths if path.suffix.lower() not in {".md", ".txt"}]

    ingest_results: dict[str, object] = {"text_items": None, "files": None}
    if text_item_paths:
        print(f"Ingesting {len(text_item_paths)} text file(s)...")
        items = []
        for path in text_item_paths:
            items.append(
                {
                    "title": path.stem,
                    "content": path.read_text(encoding="utf-8", errors="ignore"),
                    "source_type": "markdown" if path.suffix.lower() == ".md" else "text",
                    "metadata": {"source_file": str(path)},
                }
            )
        ingest_results["text_items"] = await client.ingest_text_items(
            items=items,
            force_reingest=FORCE_REINGEST,
        )

    if upload_paths:
        print(f"Ingesting {len(upload_paths)} uploaded file(s)...")
        ingest_results["files"] = await client.ingest_files(
            file_paths=upload_paths,
            force_reingest=FORCE_REINGEST,
        )

    save_json(
        INGEST_STATE_PATH,
        {
            "status": "completed",
            "files": [str(path) for path in ingest_paths],
            "result": ingest_results,
        },
    )


# ---------------------------------------------------------------------------
# Chat collection
# ---------------------------------------------------------------------------

async def collect_chat_results(client: BackendRagClient, samples: list[dict]) -> list[dict]:
    print("[2/4] Collecting chat responses...")
    existing = {} if RESET_BACKEND_BEFORE_INGEST else load_existing_chat_results()
    rows = []

    for sample in tqdm(samples, desc="Chat Samples", unit="sample"):
        sample_id = sample["sample_id"]
        if sample_id in existing:
            cached = dict(existing[sample_id])
            cached["user_input"] = sample["user_input"]
            cached["reference"] = sample["reference"]
            cached["reference_contexts"] = sample["reference_contexts"]
            cached["synthesizer_name"] = sample.get("synthesizer_name", "")
            if cached.get("retrieved_contexts"):
                rows.append(cached)
                continue

        chat_payload = await client.chat(message=sample["user_input"], top_k=TOP_K)
        row = {
            "sample_id":          sample_id,
            "user_input":         sample["user_input"],
            "reference":          sample["reference"],
            "reference_contexts": sample["reference_contexts"],
            "response":           str(chat_payload.get("answer", "")),
            "retrieved_contexts": extract_retrieved_contexts(chat_payload),
            "used_fallback":      bool(chat_payload.get("used_fallback", False)),
            "provider":           str(chat_payload.get("provider", "")),
            "model":              str(chat_payload.get("model", "")),
            "synthesizer_name":   sample.get("synthesizer_name", ""),
        }
        rows.append(row)
        if ENABLE_RATE_LIMIT_DELAY:
            await asyncio.sleep(REQUEST_DELAY_SECONDS)

    save_chat_results(rows)
    return rows


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def _col_mean(df: pd.DataFrame, col: str) -> float | None:
    if col not in df.columns:
        return None
    vals = pd.to_numeric(df[col], errors="coerce")
    return float(vals.mean()) if vals.notna().any() else None


def _summary_value(
    df: pd.DataFrame,
    col: str,
    previous_summary: dict | None,
    section: str,
) -> float | None:
    current = _col_mean(df, col)
    if current is not None:
        return current
    if not previous_summary:
        return None
    section_payload = previous_summary.get(section)
    if not isinstance(section_payload, dict):
        return None
    value = section_payload.get(col)
    return float(value) if isinstance(value, (int, float)) else None


def write_summary(df: pd.DataFrame) -> None:
    previous_summary = load_json(SUMMARY_PATH) if SUMMARY_PATH.exists() else None
    summary = {
        "sample_count": int(len(df)),
        "models": {
            "eval_generation_profile": EVAL_GENERATION_PROFILE,
            "eval_embedding_profile":  EVAL_EMBEDDING_PROFILE,
            "critic_model":            CRITIC_MODEL,
        },
        "response_quality": {
            "answer_correctness": _summary_value(
                df, "answer_correctness", previous_summary, "response_quality"),
            "answer_relevancy": _summary_value(
                df, "answer_relevancy", previous_summary, "response_quality"),
            "faithfulness": _summary_value(
                df, "faithfulness", previous_summary, "response_quality"),
        },
        "contextual_accuracy": {
            "contextual_precision": _summary_value(
                df, "contextual_precision", previous_summary, "contextual_accuracy"),
            "contextual_recall": _summary_value(
                df, "contextual_recall", previous_summary, "contextual_accuracy"),
            "contextual_relevancy": _summary_value(
                df, "contextual_relevancy", previous_summary, "contextual_accuracy"),
            "hit_rate": _summary_value(
                df, "hit_rate", previous_summary, "contextual_accuracy"),
            "mrr": _summary_value(
                df, "mrr", previous_summary, "contextual_accuracy"),
        },
    }
    save_json(SUMMARY_PATH, summary)
    print("\n=== Evaluation Summary ===")
    for section, values in summary.items():
        if isinstance(values, dict):
            print(f"\n[{section}]")
            for k, v in values.items():
                print(f"  {k}: {f'{v:.4f}' if isinstance(v, float) else v}")


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

async def async_main() -> None:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Set OPENAI_API_KEY in the environment before running.")
    os.environ["OPENAI_API_KEY"] = api_key

    ensure_run_dir()
    print("Starting DeepEval RAG evaluation run...")
    print(f"Dataset  : {DATASET_PATH}")
    print(f"Output   : {RUN_DIR}")

    dataset_df = load_dataset_df()
    if dataset_has_metric_columns(dataset_df):
        present_metric_cols = [
            col for col in SUMMARY_METRIC_COLUMNS if col in dataset_df.columns
        ]
        print(
            "Dataset already contains evaluation metric columns. "
            "Skipping backend chat collection and metric recomputation."
        )
        print(f"Using dataset metrics: {present_metric_cols}")
        save_metric_results(dataset_df)
        print("[4/4] Writing summary...")
        write_summary(dataset_df)
        print("Evaluation complete.")
        return

    samples = load_samples()
    print(f"Loaded {len(samples)} evaluation sample(s).")
    if ALLOWED_SYNTHESIZER_NAMES:
        print(f"Included sample types: {sorted(ALLOWED_SYNTHESIZER_NAMES)}")
    ingest_files = resolve_ingest_files()
    print(f"Resolved {len(ingest_files)} ingest file(s) for backend alignment.")

    # ------------------------------------------------------------------
    # Step 1 — ingest + collect chat results from the RAG backend
    # ------------------------------------------------------------------
    async with BackendRagClient(
        base_url=BASE_URL,
        username=USERNAME,
        password=PASSWORD,
        timeout_seconds=BACKEND_TIMEOUT_SECONDS,
    ) as client:
        await client.login()
        print("Login successful.")
        print(
            f"Setting eval model: "
            f"generation_profile={EVAL_GENERATION_PROFILE}, "
            f"embedding_profile={EVAL_EMBEDDING_PROFILE}"
        )
        model_selection = await client.update_model_selection(
            generation_profile=EVAL_GENERATION_PROFILE,
            embedding_profile=EVAL_EMBEDDING_PROFILE,
        )
        if SYSTEM_PROMPT_OVERRIDE.strip():
            print("Applying system prompt override for this evaluation run...")
            await client.update_system_prompt(SYSTEM_PROMPT_OVERRIDE.strip())
        active_prompt = await client.get_system_prompt()
        save_json(
            RUN_CONFIG_PATH,
            build_run_config(
                dataset_path=DATASET_PATH,
                ingest_files=ingest_files,
                model_selection=model_selection,
                system_prompt_payload=active_prompt,
            ),
        )
        print(
            "Eval model active: "
            f"{model_selection.get('generation_provider')}/{model_selection.get('generation_model')} "
            f"with embeddings "
            f"{model_selection.get('embedding_provider')}/{model_selection.get('embedding_model')}"
        )
        await ingest_docx_if_needed(client)
        rows = await collect_chat_results(client, samples)

    if not rows:
        raise RuntimeError("No chat results were collected.")

    # ------------------------------------------------------------------
    # Step 2 — load or initialise the metric dataframe.
    # Columns already present in a previous run's CSV are preserved and
    # their metrics are skipped by each evaluate_*_if_needed call below.
    # ------------------------------------------------------------------
    base_df = pd.DataFrame(rows)
    save_metric_results(base_df)

    metric_df = base_df.copy() if RESET_BACKEND_BEFORE_INGEST else load_metric_results()
    if metric_df.empty:
        metric_df = base_df.copy()

    # ------------------------------------------------------------------
    # Step 3 — compute metrics, skipping any column already present
    # ------------------------------------------------------------------
    print("[3/4] Evaluating metrics (skipping columns already present)...")

    # Retrieval metrics first — they enrich the rows list
    rows, metric_df = await compute_retrieval_metrics_if_needed(
        rows=rows,
        base_df=metric_df,
    )

    # All DeepEval metrics share the same skip-if-column-exists pattern
    for col_name, metric in DEEPEVAL_METRICS.items():
        metric_df = await evaluate_deepeval_metric_if_needed(
            col_name=col_name,
            metric=metric,
            rows=rows,
            base_df=metric_df,
        )

    # ------------------------------------------------------------------
    # Step 4 — persist and summarise
    # ------------------------------------------------------------------
    save_metric_results(metric_df)
    print("[4/4] Writing summary...")
    write_summary(metric_df)
    print("Evaluation complete.")


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
