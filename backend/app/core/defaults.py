CHAT_REPEATED_PROMPT_LOOKBACK = 5
CHAT_MAX_CONTEXT_CHUNK_CHARS = 1800
CHAT_MIN_TOP_K = 3
CHAT_MAX_TOP_K = 8
CHAT_MAX_RESPONSE_CHARS = 2000
CHAT_MAX_RESPONSE_TOKENS = 700
CHAT_TOP_P = 1.0
CHAT_FREQUENCY_PENALTY = 0.0
CHAT_PRESENCE_PENALTY = 0.0
EMBEDDING_CACHE_TTL_SECONDS = 3600
SESSION_TTL_SECONDS = 1800
SESSION_STORAGE_ENABLED = False
OPENAI_REASONING_EFFORT = "low"
NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
RERANK_BASE_URL = "https://ai.api.nvidia.com/v1/retrieval"
DEFAULT_GENERATION_CATALOG = [
    {
        "profile_name": "openai_gpt41_mini",
        "provider": "openai",
        "model": "gpt-4.1-mini",
    },
    {
        "profile_name": "nim_3super120",
        "provider": "nim",
        "model": "nvidia/nemotron-3-super-120b-a12b",
    },
    {
        "profile_name": "ollama_llama32",
        "provider": "ollama",
        "model": "llama3.2",
    },
]
DEFAULT_EMBEDDING_CATALOG = [
    {
        "profile_name": "openai_small_1536",
        "provider": "openai",
        "model": "text-embedding-3-small",
        "dimension": 1536,
    },
    {
        "profile_name": "ollama_1536",
        "provider": "ollama",
        "model": "rjmalagon/gte-qwen2-1.5b-instruct-embed-f16",
        "dimension": 1536,
    },
    {
        "profile_name": "ollama_4096",
        "provider": "ollama",
        "model": "qwen3-embedding",
        "dimension": 4096,
    },
    {
        "profile_name": "nim_nemotron_2048",
        "provider": "nim",
        "model": "nvidia/llama-nemotron-embed-1b-v2",
        "dimension": 2048,
    },
]
