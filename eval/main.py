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
from backend_rag_client import BackendRagClient
from tqdm.auto import tqdm
import pandas as pd
from pathlib import Path
from datetime import datetime
import json
import ast
import asyncio

BASE_URL = "http://localhost:9010"
USERNAME = ""
PASSWORD = ""
OPENAI_API_KEY = ""

DOCS_DIR = Path("eval/dataset").resolve()
DATASET_PATH = Path("eval/dataset/metric_results.csv").resolve()
PRIMARY_INGEST_FILE = DOCS_DIR / "snaic_overview.md"
RUNS_ROOT = Path("eval/output").resolve()
RUN_DIR = (RUNS_ROOT / datetime.now().strftime("%Y%m%d_%H%M%S")).resolve()
RAW_RESULTS_PATH = RUN_DIR / "chat_results.jsonl"
METRIC_RESULTS_PATH = RUN_DIR / "metric_results.csv"
SUMMARY_PATH = RUN_DIR / "summary.json"
INGEST_STATE_PATH = RUN_DIR / "ingest_state.json"

RESET_BACKEND_BEFORE_INGEST = False
FORCE_REINGEST = False
TOP_K = 5
ENABLE_RATE_LIMIT_DELAY = False
REQUEST_DELAY_SECONDS = 0.1
INCLUDED_SYNTHESIZER_NAMES = {"answerable"}

EVAL_GENERATION_PROFILE = "nim_3super120"
EVAL_EMBEDDING_PROFILE = "nim_nemotron_2048"

CRITIC_MODEL = "gpt-4.1-mini"
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

# DeepEval picks up the key from the environment; set it once here so every
# metric constructed below inherits it automatically.
os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY

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
    return AsyncOpenAI(api_key=OPENAI_API_KEY)


# ---------------------------------------------------------------------------
# Dataset loading
# ---------------------------------------------------------------------------

def load_samples() -> list[dict]:
    df = pd.read_csv(DATASET_PATH)
    df = df.fillna("")
    if "synthesizer_name" in df.columns and INCLUDED_SYNTHESIZER_NAMES:
        df = df[df["synthesizer_name"].isin(
            INCLUDED_SYNTHESIZER_NAMES)].reset_index(drop=True)
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


def load_metric_results() -> pd.DataFrame:
    if METRIC_RESULTS_PATH.exists():
        return pd.read_csv(METRIC_RESULTS_PATH).fillna("")
    return pd.DataFrame()


def save_metric_results(df: pd.DataFrame) -> None:
    df.to_csv(METRIC_RESULTS_PATH, index=False, encoding="utf-8-sig")


def extract_retrieved_contexts(chat_payload: dict) -> list[str]:
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


# ---------------------------------------------------------------------------
# row -> LLMTestCase
# ---------------------------------------------------------------------------

def row_to_test_case(row: dict) -> LLMTestCase:
    """Convert a chat-result row into a DeepEval LLMTestCase.

    Field mapping
    -------------
    input             <- user_input           (the question asked)
    actual_output     <- response             (what the RAG backend returned)
    expected_output   <- reference            (ground-truth answer from testset)
    retrieval_context <- retrieved_contexts   (what the retriever actually fetched)
    context           <- reference_contexts   (ground-truth supporting passages)
    """
    return LLMTestCase(
        input=row["user_input"],
        actual_output=row["response"],
        expected_output=row.get("reference", ""),
        retrieval_context=row.get("retrieved_contexts", []),
        context=row.get("reference_contexts", []),
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
    if col_name in base_df.columns:
        print(f"  {col_name}: column already present — skipping.")
        return base_df

    print(f"  Computing {col_name} ...")
    scores: list[float | None] = []

    for row in tqdm(rows, desc=col_name, unit="sample"):
        test_case = row_to_test_case(row)
        try:
            # DeepEval's internal Rich indicator renders poorly in some IDE
            # consoles and appears as a static bar. Disable it and let the
            # outer tqdm loop show real per-sample progress instead.
            await metric.a_measure(test_case, _show_indicator=False)
            scores.append(float(metric.score))
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
    ref_ctx = "\n\n".join(
        reference_contexts[:2]) if reference_contexts else "None"
    prompt = (
        f"Decide whether the candidate retrieved chunk is relevant enough to "
        f"support answering the question correctly.\n\n"
        f"Question:\n{question}\n\n"
        f"Reference Answer:\n{reference}\n\n"
        f"Reference Context:\n{ref_ctx}\n\n"
        f"Candidate Chunk:\n{candidate_chunk}\n\n"
        f"Reply with exactly one word: YES or NO"
    )
    resp = await client.chat.completions.create(
        model=CRITIC_MODEL,
        temperature=0,
        max_tokens=1,
        logprobs=True,
        top_logprobs=5,
        messages=[{"role": "user", "content": prompt}],
    )
    # Use logprob mass on YES vs NO tokens for a soft, calibrated verdict
    top = resp.choices[0].logprobs.content[0].top_logprobs
    yes_prob = sum(math.exp(e.logprob)
                   for e in top if e.token.strip().upper() == "YES")
    no_prob = sum(math.exp(e.logprob)
                  for e in top if e.token.strip().upper() == "NO")
    if yes_prob + no_prob == 0:
        return resp.choices[0].message.content.strip().upper() == "YES"
    return yes_prob >= no_prob


async def compute_retrieval_metrics_if_needed(
    *,
    rows: list[dict],
    base_df: pd.DataFrame,
) -> tuple[list[dict], pd.DataFrame]:
    """Compute hit_rate and mrr if either column is missing from base_df.

    Both columns are computed together in one pass since they share the same
    per-chunk judge calls.  If both already exist the entire pass is skipped.
    """
    if {"hit_rate", "mrr"}.issubset(base_df.columns):
        print("  hit_rate / mrr: columns already present — skipping.")
        return rows, base_df

    print("  Computing hit_rate and mrr (ARES chunk-relevance judge)...")
    client = _openai_client()
    enriched: list[dict] = []

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
        if ENABLE_RATE_LIMIT_DELAY:
            await asyncio.sleep(REQUEST_DELAY_SECONDS)

    save_chat_results(enriched)
    enriched_df = pd.DataFrame(enriched)
    save_metric_results(enriched_df)
    return enriched, enriched_df


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

    if PRIMARY_INGEST_FILE.exists():
        print(f"Ingesting primary dataset file: {PRIMARY_INGEST_FILE.name}")
        content = PRIMARY_INGEST_FILE.read_text(encoding="utf-8")
        result = await client.ingest_text_items(
            items=[
                {
                    "title": PRIMARY_INGEST_FILE.stem,
                    "content": content,
                    "source_type": "markdown",
                    "metadata": {"source_file": PRIMARY_INGEST_FILE.name},
                }
            ],
            force_reingest=FORCE_REINGEST,
        )
        save_json(
            INGEST_STATE_PATH,
            {"status": "completed", "mode": "primary_markdown",
             "files": [str(PRIMARY_INGEST_FILE)], "result": result},
        )
        return

    docx_paths = sorted(DOCS_DIR.glob("*.docx"))
    if not docx_paths:
        print("No primary markdown file or .docx files found. Skipping ingest step.")
        save_json(INGEST_STATE_PATH, {
                  "status": "skipped", "reason": "no_ingest_source_found"})
        return

    print(f"Ingesting {len(docx_paths)} .docx file(s)...")
    result = await client.ingest_files(file_paths=docx_paths, force_reingest=FORCE_REINGEST)
    save_json(
        INGEST_STATE_PATH,
        {"status": "completed", "mode": "docx_files",
         "files": [str(p) for p in docx_paths], "result": result},
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


def write_summary(df: pd.DataFrame) -> None:
    summary = {
        "sample_count": int(len(df)),
        "models": {
            "eval_generation_profile": EVAL_GENERATION_PROFILE,
            "eval_embedding_profile":  EVAL_EMBEDDING_PROFILE,
            "critic_model":            CRITIC_MODEL,
        },
        "response_quality": {
            "answer_correctness": _col_mean(df, "answer_correctness"),
            "answer_relevancy":   _col_mean(df, "answer_relevancy"),
            "faithfulness":       _col_mean(df, "faithfulness"),
        },
        "contextual_accuracy": {
            "contextual_precision": _col_mean(df, "contextual_precision"),
            "contextual_recall":    _col_mean(df, "contextual_recall"),
            "contextual_relevancy": _col_mean(df, "contextual_relevancy"),
            "hit_rate":             _col_mean(df, "hit_rate"),
            "mrr":                  _col_mean(df, "mrr"),
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
    if INCLUDED_SYNTHESIZER_NAMES:
        print(f"Included sample types: {sorted(INCLUDED_SYNTHESIZER_NAMES)}")

    # ------------------------------------------------------------------
    # Step 1 — ingest + collect chat results from the RAG backend
    # ------------------------------------------------------------------
    async with BackendRagClient(
        base_url=BASE_URL, username=USERNAME, password=PASSWORD
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
