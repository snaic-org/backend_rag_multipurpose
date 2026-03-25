# RAG Pipeline

## Pipeline steps

1. Accept user query on `/chat` or `/chat/stream`
2. Rate-limit the request in Redis and apply the daily per-user quota
3. Filter the input for blocked phrases, unusually long prompts, and repeated prompt abuse
4. Resolve generation provider/model from the request or config defaults
5. Resolve the embedding profile from the request or config defaults
6. Embed the query with the selected provider/model using `input_type=query` for NVIDIA NIM-compatible embedding models
7. Clamp `top_k` to the configured safe range
8. Retrieve a slightly larger candidate pool from the Qdrant collection for that embedding dimension
9. Filter vector results using `SIMILARITY_THRESHOLD`
10. If vector retrieval returns nothing, run a lexical fallback against stored titles and chunk content
11. If lexical retrieval still returns nothing, use the best available semantic matches without applying the threshold
12. If reranking is enabled, send the retrieved candidates through the reranker before prompt construction
13. If no chunks exist for the active embedding pair, return the safe fallback
14. Build a grounded prompt from retrieved chunks with history, context, and chunk-size caps
15. Generate the answer using the selected generation provider
16. Truncate the final answer to the configured output budget
17. Return citations and metadata
18. Optionally store session messages in Redis

## Retrieval query

The implementation uses cosine similarity in Qdrant:

Results are filtered by the embedding profile and searched with cosine similarity.

Results are filtered by:

- `embedding_provider`
- `embedding_model`
- `similarity_threshold`
- `top_k`
- `rerank_enabled`
- `rerank_model`
- `rerank_invoke_url`

The service layer also enforces:

- authenticated-user request budgets
- prompt length caps
- output size caps
- prompt-injection phrase blocking
- repeated-prompt detection

## Grounding behavior

The system prompt explicitly instructs the model to:

- answer only from the provided context
- not invent services, pricing, experience, or facts
- say it does not know when context is insufficient
- stay professional, friendly, cheerful, and grounded

## Fallback behavior

If vector retrieval returns no chunks above the threshold, the system tries a second-stage lexical lookup over the stored chunk payloads. If that still returns nothing, it falls back to the best available semantic matches for the active embedding profile. Only if no chunks exist at all for that profile does the system return:

```text
I couldn't find that in the knowledge base.
```

This is returned for:

- `POST /chat`
- `POST /chat/stream`

## Citations

Citations are built from retrieved chunks and include:

- `document_id`
- `chunk_id`
- `title`
- `url`
- `source_type`
- `snippet`
- `metadata`

## Current limitations

- No multi-index support for different embedding dimensions
- No document deletion endpoint
- Broad questions over tiny corpora may still retrieve weak matches because the final fallback prefers grounded recall over an empty answer
- Token budgets are approximate because the implementation uses whitespace-based counting rather than a dedicated tokenizer
- Reranking is optional and only helps when the initial retrieval pool contains useful candidates

Current default indexed embedding setup:

- profile: `ollama_1536`
- provider: `ollama`
- model: `rjmalagon/gte-qwen2-1.5b-instruct-embed-f16`
- dimension: `1536`
- similarity threshold: `0.35`
