# Evaluation

This folder contains the live RAG evaluation workflow for this repository.

The evaluator runs against the real backend API, not a mocked pipeline. It logs in through `/auth/token`, optionally clears backend state with `/admin/reset`, ingests benchmark contexts through `/ingest/text`, asks benchmark questions through `/chat` with `debug=true`, scores the results with LangChain evaluators, and writes run artifacts to `eval/output/<timestamp>/`.

## What This Evaluates

The evaluation covers four areas:

- retrieval quality: whether the backend returns benchmark-relevant chunks
- answer quality: whether the answer matches the expected answer
- grounding: whether the answer stays supported by retrieved context
- operational behavior: answer rate, fallback rate, citations, and ingest stats

Because the script uses the live API flow, the results reflect the backend's actual:

- auth flow
- ingestion pipeline
- chunking behavior
- embedding settings
- retrieval and reranking behavior
- generation provider and model selection
- citation output

## Files In This Folder

- `eval/config.py`: main editable defaults for dataset, sample size, models, and judge settings
- `eval/langchain_eval.py`: main evaluator
- `eval/run-langchain-eval.ps1`: PowerShell wrapper for local runs
- `eval/requirements.txt`: evaluator-only Python dependencies
- `eval/output/`: timestamped output artifacts from completed runs

## Prerequisites

Before running an eval:

1. Start the backend and make sure it is reachable.
2. Ensure auth is enabled or configured as expected for the target environment.
3. Ensure the configured admin credentials are valid.
4. Ensure the backend can ingest text and answer `/chat` requests successfully.
5. If you want LLM-based LangChain judge metrics, configure judge models.

The evaluator reads defaults from `backend/.env` when possible:

- `APP_PORT` for the default base URL
- `AUTH_BOOTSTRAP_ADMIN_USERNAME` for the default username
- `AUTH_BOOTSTRAP_ADMIN_PASSWORD` for the default password

If `--base-url` is not provided, the evaluator defaults to `http://localhost:<APP_PORT>` and falls back to `http://localhost:9010`.

## Config-First Defaults

Mutable eval defaults now live in `eval/config.py`.

Edit that file when you want to change the normal default behavior for:

- dataset source and dataset selection
- sample size
- retrieval `top_k`
- generation provider and model
- embedding provider and model
- judge metric defaults
- judge RPM cap

Base URL and authentication are intentionally kept outside `eval/config.py`.

`eval/config.py` also includes a built-in Hugging Face dataset preset map:

- `HF_DATASET_PRESETS`
- `ACTIVE_HF_DATASET_PRESET`

That is the safest way to switch common datasets because the preset defines the matching:

- `hf_dataset`
- `hf_config`
- `hf_split`

Normal usage pattern:

1. edit `eval/config.py`
2. run `.\eval\run-langchain-eval.ps1`
3. override specific flags only when needed for one-off runs

## Install

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r eval\requirements.txt
```

Dependencies in `eval/requirements.txt`:

- `httpx`
- `datasets`
- `langchain`
- `langchain-openai`
- `rapidfuzz`
- `pandas`

## How The Evaluator Works

For each run, the evaluator:

1. Loads benchmark samples from Hugging Face or a local JSONL file.
2. Extracts `question`, `ground_truth`, and `reference_contexts`.
3. Deduplicates benchmark contexts by content hash.
4. Ingests those contexts through `/ingest/text` in batches.
5. Sends each benchmark question to `/chat` with `debug=true`.
6. Collects:
   - backend answer
   - retrieved chunks
   - citations
   - fallback usage
   - provider and model used
7. Runs LangChain evaluator metrics over the collected data.
8. Computes additional lexical and operational summary metrics.
9. Writes raw and scored artifacts to `eval/output/<timestamp>/`.

## Backend Contract Required By This Eval

This evaluator assumes the backend supports:

- `POST /auth/token`
- `DELETE /admin/reset`
- `POST /ingest/text`
- `POST /chat`

It also assumes `/chat` returns retrieved chunks when `debug=true`, because retrieval scoring depends on those returned contexts.

The backend does not need a special eval mode, but it must expose:

- `answer`
- `retrieved_chunks`
- `citations`
- `used_fallback`
- `provider`
- `model`

## Dataset Sources

The script supports two dataset modes.

Out of the box, the loader already handles:

- Amnesty QA style records with `question`, `ground_truth`, and `reference_contexts`
- MultiHop-RAG style records with `query`, `answer`, and `evidence_list`
- JSONL records using the accepted aliases documented below

### Hugging Face dataset

Default:

- dataset: `explodinggradients/amnesty_qa`
- config: `english_v3`
- split: `eval`

Basic run:

```powershell
python eval\langchain_eval.py `
  --base-url http://localhost:9010 `
  --sample-size 10
```

Explicit dataset selection:

```powershell
python eval\langchain_eval.py `
  --dataset-source huggingface `
  --hf-dataset explodinggradients/amnesty_qa `
  --hf-config english_v3 `
  --hf-split eval `
  --base-url http://localhost:9010 `
  --sample-size 10
```

If a selected Hugging Face dataset depends on a legacy dataset script that the installed `datasets` version no longer supports, the script raises a clear error and recommends switching datasets or using local JSONL input.

### MultiHop-RAG

`MultiHop-RAG` is a good fit for this evaluator because it includes multi-hop questions plus supporting evidence. The loader now accepts MultiHop-RAG-style fields directly:

- question from `query`
- answer from `answer`
- reference contexts from `evidence_list`

Example:

```powershell
python eval\langchain_eval.py `
  --dataset-source huggingface `
  --hf-dataset yixuantt/MultiHopRAG `
  --hf-split train `
  --base-url http://localhost:9010 `
  --sample-size 25 `
  --top-k 8
```

Use a higher `--top-k` than single-hop benchmarks if you want to measure whether the retriever can surface multiple supporting facts.

Important config note:

- `yixuantt/MultiHopRAG` expects `hf_config=MultiHopRAG`
- `explodinggradients/amnesty_qa` expects `hf_config=english_v3`

Those mappings are defined in `eval/config.py`. If you switch datasets, prefer changing `ACTIVE_HF_DATASET_PRESET` instead of editing only one of `hf_dataset` or `hf_config`.

### Other dataset options

If you want more than the small `amnesty_qa` eval split, these are practical options:

- `yixuantt/MultiHopRAG`
  - best fit for this evaluator
  - already matches the current loader with `query`, `answer`, and `evidence_list`
  - good default choice when you want `SampleSize > 50`
- `UltraRAG/UltraRAG_Benchmark`
  - benchmark suite with larger evaluation subsets such as NQ, TriviaQA, PopQA, HotpotQA, and 2WikiMultiHopQA
  - useful when you want a broader benchmark source
- `cmriat/2wikimultihopqa`
  - larger multi-hop QA dataset
  - useful for retrieval plus reasoning stress tests
  - usually needs schema conversion before direct use in this evaluator
- `hotpotqa/hotpot_qa`
  - classic multi-hop QA benchmark with large splits
  - good for multi-document retrieval and reasoning
  - usually needs preprocessing from its native context structure
- `akariasai/PopQA`
  - large open-domain QA benchmark
  - useful for answer accuracy testing
  - usually needs explicit reference context construction for this evaluator
- `G4KMU/t2-ragbench`
  - newer RAG benchmark family
  - useful if you want larger, more recent benchmark coverage
  - expect preprocessing or schema adaptation before direct use

Recommended order:

1. `yixuantt/MultiHopRAG`
2. local JSONL built from your own domain data
3. one of the larger benchmark suites after preprocessing

If you request `SampleSize=100` but the selected split only contains 20 usable rows, the evaluator will load only those 20 rows and print the actual loaded sample size.

### FRAMES

`FRAMES` can be used with this evaluator, but not usually as raw Hugging Face input.

Reason:

- this evaluator ingests context text into the backend
- the raw FRAMES benchmark is commonly distributed with question and answer fields plus source links
- links alone are not enough for `/ingest/text`

Recommended FRAMES workflow:

1. Export or preprocess FRAMES into local JSONL.
2. Resolve each sample's linked sources into plain text passages.
3. Store those passages in `reference_contexts`.
4. Run the evaluator with `--dataset-source jsonl`.

Expected JSONL shape:

```json
{
  "id": "frames-001",
  "question": "Your FRAMES question here",
  "ground_truth": "Expected answer here",
  "reference_contexts": [
    "Resolved source passage 1",
    "Resolved source passage 2"
  ],
  "metadata": {
    "source_dataset": "FRAMES"
  }
}
```

Example run:

```powershell
python eval\langchain_eval.py `
  --dataset-source jsonl `
  --dataset-path .\eval\frames-benchmark.jsonl `
  --base-url http://localhost:9010 `
  --sample-size 25 `
  --top-k 8
```

### Local JSONL dataset

Use this when you want to test domain-specific content or production-like prompts and contexts.

Each JSONL line must provide:

- a question-like field: `question`, `user_input`, or `query`
- an answer-like field: `ground_truth`, `reference`, `answer`, `reference_answer`, or `ideal_answer`
- a context-like field: `reference_contexts`, `reference_context`, `retrieved_contexts`, `ground_truth_contexts`, `contexts`, `context`, or `documents`

The JSONL path is the recommended way to evaluate any benchmark that does not already ship the source passages as plain text, including FRAMES-style datasets built from links or external documents.

Minimal example:

```json
{
  "id": "sample-1",
  "question": "What does the policy say about refunds?",
  "ground_truth": "Refunds are allowed within 30 days with proof of purchase.",
  "reference_contexts": [
    "Refunds are allowed within 30 days with proof of purchase."
  ]
}
```

Run it with:

```powershell
python eval\langchain_eval.py `
  --dataset-source jsonl `
  --dataset-path .\eval\my-benchmark.jsonl `
  --base-url http://localhost:9010 `
  --reset-first `
  --sample-size 25
```

## Judge Model Configuration

Ground-truth comparison is the default.

If your dataset already includes `ground_truth`, the evaluator can score the backend answer against that reference without using a second model. That is now the default behavior because it is:

- faster
- cheaper
- easier to interpret
- not dependent on external judge RPM limits

Judge metrics are optional and are now opt-in.

The evaluator now follows backend-style defaults automatically:

- judge LLM model defaults to `DEFAULT_GENERATION_MODEL` when `DEFAULT_GENERATION_PROVIDER=nim`
- judge embedding model defaults to `DEFAULT_EMBEDDING_MODEL` when `DEFAULT_EMBEDDING_PROVIDER=nim`
- judge base URL defaults to `NIM_BASE_URL`
- judge API key defaults to `NIM_API_KEY`

If you need to override the judge backend without editing code, use environment variables before the run:

- `LANGCHAIN_EVAL_LLM_MODEL`
- `LANGCHAIN_EVAL_LLM_BASE_URL`
- `LANGCHAIN_EVAL_LLM_API_KEY`
- `LANGCHAIN_EVAL_EMBED_MODEL`
- `LANGCHAIN_EVAL_EMBED_BASE_URL`
- `LANGCHAIN_EVAL_EMBED_API_KEY`

If you want judge-based semantic scoring, you can enable it with:

- `--judge-metrics all`
- `--judge-metrics correctness,relevance`
- `--judge-metrics groundedness`
- `--judge-metrics embedding`
- `--judge-max-rpm 36`

This is useful with NIM rate limits. For example, `10` samples with `all` can trigger roughly:

- `30` LLM judge calls for correctness, relevance, and groundedness
- `20` embedding requests for embedding similarity

Use `--judge-max-rpm` below the provider cap for headroom. If NIM allows `40 RPM`, `36` is a safer default than `40`.

By default, `--judge-metrics none` is used.

With no judge metrics enabled, the run still computes:

- backend outputs
- exact match against ground truth
- token precision, token recall, and token F1 against ground truth
- lexical answer similarity
- lexical context match
- lexical retrieval recall/precision style metrics against reference contexts
- operational summary metrics
- lexical answer and retrieval diagnostics

## Main CLI Options

Common backend and dataset options:

- `--base-url`
- `--username`
- `--password`
- `--dataset-source huggingface|jsonl`
- `--dataset-path`
- `--hf-dataset`
- `--hf-config`
- `--hf-split`
- `--hf-trust-remote-code`
- `--sample-size`
- `--top-k`
- `--batch-size`
- `--output-dir`

Backend behavior overrides:

- `--reset-first`: calls `/admin/reset` before ingest
- `--force-reingest`: sends `force_reingest=true` to `/ingest/text`
- `--embedding-provider`
- `--embedding-model`
- `--generation-provider`
- `--generation-model`
- `--judge-metrics`
- `--judge-max-rpm`

## PowerShell Wrapper

For local Windows runs, use:

```powershell
.\eval\run-langchain-eval.ps1
```

The wrapper:

- reads mutable defaults from `eval/config.py`
- reads `backend/.env`
- defaults the base URL from `APP_PORT`
- defaults the username from `AUTH_BOOTSTRAP_ADMIN_USERNAME`
- defaults the password from `AUTH_BOOTSTRAP_ADMIN_PASSWORD`
- forwards auth and base URL to `eval/langchain_eval.py`

Useful examples:

```powershell
.\eval\run-langchain-eval.ps1
```

```powershell
.\eval\run-langchain-eval.ps1 -BaseUrl http://localhost:9010
```

```powershell
.\eval\run-langchain-eval.ps1 -Username admin -Password change-me-immediately
```

## Metrics Produced

The script uses these LangChain-based metrics when the required judge models are available:

- labeled answer correctness
- answer relevance criteria scoring
- context-groundedness scoring
- embedding-distance similarity

It also computes repo-specific summary metrics that work even without full judge coverage:

- `answered_rate`
- `fallback_rate`
- `retrieval_hit_rate`
- `avg_retrieved_contexts`
- `avg_citations`
- `avg_response_chars`
- `avg_context_match`
- `avg_reference_recall_at_k_lexical`
- `avg_context_precision_at_k_lexical`
- `reference_recall_at_k_hit_rate`
- `avg_exact_match`
- `avg_token_precision`
- `avg_token_recall`
- `avg_token_f1`
- `exact_match_rate`
- `context_match_rate_at_0_85`
- `avg_answer_similarity`
- `answer_match_rate_at_0_70`
- `avg_quality_score`
- `quality_pass_rate_at_0_70`

Lexical helper metrics use `rapidfuzz`:

- context match threshold: `0.85`
- answer match threshold: `0.70`

`quality_score` is a per-sample average of whichever answer-quality metrics are present from:

- `langchain_answer_correctness`
- `langchain_answer_relevance`
- `langchain_groundedness`
- `langchain_embedding_similarity`

If no judge-backed quality metrics are available, `quality_score` remains `null`.

## Output Artifacts

Each run writes to `eval/output/<timestamp>/`:

- `config.json`: configuration snapshot for the run
- `summary.json`: grouped aggregate metrics and weakest samples
- `backend_results.jsonl`: raw backend outputs before LangChain scoring
- `langchain_scored_results.jsonl`: backend outputs plus per-sample LangChain and derived metrics
- `langchain_scored_results.csv`: CSV export of the scored results

### `summary.json` structure

The summary groups metrics into:

- `run`: run metadata such as sample count, requested limit, and configured judge models
- `operational`: answer rate, fallback rate, citation density, and ingest counts
- `retrieval`: retrieval-oriented lexical context-match metrics
- `generation`: answer-quality metrics, lexical answer similarity, and blended quality scores
- `raw_langchain_metrics`: average raw LangChain metric values
- `weakest_samples`: lowest-quality samples for quick failure inspection

### `backend_results.jsonl` fields

Each row contains:

- `sample_id`
- `question`
- `ground_truth`
- `reference_contexts`
- `response`
- `retrieved_contexts`
- `citations`
- `used_fallback`
- `provider`
- `model`
- `metadata`

### `langchain_scored_results.*` fields

The scored outputs extend backend rows with whatever LangChain and derived fields are available, including:

- `langchain_answer_correctness`
- `langchain_answer_relevance`
- `langchain_groundedness`
- `langchain_embedding_similarity`
- `answer_similarity`
- `retrieval_context_match`
- `quality_score`

## Recommended Run Patterns

Use the same settings you expect to run in production-like environments:

- same chunking behavior
- same embedding provider and model
- same retrieval `top_k`
- same generation provider and model
- same prompt and guardrail behavior

Otherwise the result is mostly measuring a synthetic test configuration rather than the system you plan to ship.

## Important Notes

- `--reset-first` deletes backend state. Use it only against a safe environment.
- The evaluator ingests unique benchmark contexts only once per run, deduplicated by content hash.
- `--force-reingest` is useful when you want to bypass backend deduplication behavior during repeated runs.
- A run can succeed even if only non-LLM metrics are available.
- If the backend returns no retrieved chunks for `debug=true`, retrieval evaluation will be incomplete or misleading.
- If your benchmark includes intentionally hard or unanswerable questions, interpret answer similarity together with fallback behavior and weakest samples.

## Suggested Benchmark Design

For meaningful RAG validation, prefer a benchmark that includes:

- answerable questions grounded in ingested contexts
- paraphrased questions, not only exact-span lookups
- multi-sentence reference answers where needed
- negative or hard questions that test abstention and fallback behavior
- multiple documents when you want to validate cross-document retrieval

For each sample, keep:

- `id`
- `question`
- `ground_truth`
- `reference_contexts`
- optional metadata such as source, topic, or difficulty

## Troubleshooting

Common issues:

- login failure: verify `AUTH_BOOTSTRAP_ADMIN_USERNAME` and `AUTH_BOOTSTRAP_ADMIN_PASSWORD`
- connection failure: verify the backend base URL and exposed port
- empty retrieval results: verify `/chat` returns `retrieved_chunks` when `debug=true`
- weak scores after repeated runs: verify the backend was reset or that reingest settings match your intent
- missing LLM metrics: verify backend judge defaults or `LANGCHAIN_EVAL_LLM_*` environment variables
- missing semantic metrics: verify backend judge defaults or `LANGCHAIN_EVAL_EMBED_*` environment variables
- slow runs after backend evaluation: reduce `--judge-metrics` or lower `--sample-size` to stay under judge endpoint RPM limits
- long waits between judge calls: expected when `--judge-max-rpm` is intentionally throttling to stay under provider caps

## Quick Start

Small local run with backend defaults:

```powershell
.\eval\run-langchain-eval.ps1
```

Quick smoke test with no judge traffic:

```powershell
Edit `eval/config.py` to set `sample_size=5` and `judge_metrics="none"`, then run:

.\eval\run-langchain-eval.ps1
```

NIM-safe accuracy run with visible throttling:

```powershell
Edit `eval/config.py` to set:

- `sample_size = 5`
- `judge_metrics = "correctness,relevance,groundedness"`
- `judge_max_rpm = 36`

Then run:

.\eval\run-langchain-eval.ps1
```
