from __future__ import annotations

# Known Hugging Face dataset presets for this evaluator.
# Set ACTIVE_HF_DATASET_PRESET to one of these keys for the normal default run.
HF_DATASET_PRESETS = {
    "multihoprag": {
        "hf_dataset": "yixuantt/MultiHopRAG",
        "hf_config": "MultiHopRAG",
        "hf_split": "train",
        "notes": "Best current fit for this evaluator. Uses query/answer/evidence_list directly. The usable QA subset is the train split.",
    },
    "amnesty_qa": {
        "hf_dataset": "explodinggradients/amnesty_qa",
        "hf_config": "english_v3",
        "hf_split": "eval",
        "notes": "Small eval-oriented benchmark. Good for quick smoke checks.",
    },
}

# Main dataset default for config-driven runs.
ACTIVE_HF_DATASET_PRESET = "multihoprag"

_active_preset = HF_DATASET_PRESETS[ACTIVE_HF_DATASET_PRESET]

EVAL_DEFAULTS = {
    "dataset_source": "huggingface",
    "dataset_path": "",
    "hf_dataset": _active_preset["hf_dataset"],
    "hf_config": _active_preset["hf_config"],
    "hf_split": _active_preset["hf_split"],
    "hf_trust_remote_code": False,
    "sample_size": 100,
    "top_k": 5,
    "batch_size": 20,
    "reset_first": False,
    "force_reingest": False,
    "embedding_provider": "nim",
    "embedding_model": "nvidia/llama-nemotron-embed-1b-v2",
    "generation_provider": "nim",
    "generation_model": "nvidia/nemotron-3-super-120b-a12b",
    "judge_metrics": "none",
    "judge_max_rpm": 36,
    "judge_timeout_seconds": 60.0,
    "judge_llm_base_url": "https://integrate.api.nvidia.com/v1",
    "judge_embedding_base_url": "https://integrate.api.nvidia.com/v1",
    "context_match_threshold": 0.85,
    "answer_match_threshold": 0.70,
}
