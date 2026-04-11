# Eval

## Summary

This folder now has two separate workflows:

- testset generation (`eval/create_domain_test.py`) using Ragas graph/synthesizers
- runtime evaluation (`eval/main.py`) using DeepEval metrics over backend `/chat` outputs

The active evaluator for score reporting is DeepEval.

## Dataset Generation

Source documents are read from `eval/dataset/`.

`eval/create_domain_test.py`:

- builds or reuses `eval/dataset/knowledge_graph.json`
- generates mixed samples (`answerable`, `partial`, `not_found`, `unrelated`, `adversarial`)
- writes `eval/dataset/testset.csv`

Run:

```powershell
python eval\create_domain_test.py
```

## Evaluation Runtime (DeepEval)

`eval/main.py` runs end-to-end evaluation:

- login to backend
- optional reset + ingest
- call `/chat` for each sample
- score with DeepEval + judge-based retrieval metrics
- write per-run artifacts under `eval/output/ragas_eval/<timestamp>/`

Main artifacts:

- `chat_results.jsonl`
- `metric_results.csv`
- `summary.json`

Run:

```powershell
python eval\main.py
```

## Metrics In Use

DeepEval metrics in `eval/main.py`:

- `answer_relevancy`
- `faithfulness`
- `contextual_precision`
- `contextual_recall`
- `contextual_relevancy`
- `answer_correctness` (via `GEval`)

Additional retrieval metrics:

- `hit_rate`
- `mrr`

## Why LLM-Judge Relevancy

Answer relevancy is judged by an LLM (DeepEval `AnswerRelevancyMetric`), not plain cosine-only scoring.

Reason:

- embedding cosine alone can miss factual nuance and judge semantically-similar but wrong answers as acceptable
- LLM-as-judge methods generally correlate better with human grading for answer quality and groundedness

Research commonly referenced for this choice:

- G-Eval (Liu et al., 2023): https://arxiv.org/abs/2303.16634
- ARES (Saad-Falcon et al., 2023): https://arxiv.org/abs/2311.09476
- Judging LLM-as-a-Judge / MT-Bench (Zheng et al., 2023): https://arxiv.org/abs/2306.05685

## Notes

- output folder name remains `eval/output/ragas_eval/` for backward compatibility with existing scripts/artifacts
- if a metric column already exists in `metric_results.csv`, `eval/main.py` skips recomputing that metric
