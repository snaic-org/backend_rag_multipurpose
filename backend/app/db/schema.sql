CREATE TABLE IF NOT EXISTS app_users (
    id UUID PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS api_keys (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    key_prefix TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    last_used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_api_keys_user_id ON api_keys (user_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_prefix ON api_keys (key_prefix);

CREATE TABLE IF NOT EXISTS system_prompt_settings (
    id SMALLINT PRIMARY KEY CHECK (id = 1),
    system_prompt TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS model_selection_settings (
    id SMALLINT PRIMARY KEY CHECK (id = 1),
    generation_profile TEXT NOT NULL,
    embedding_profile TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY,
    content_hash TEXT,
    title TEXT NOT NULL,
    url TEXT,
    source_type TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    original_filename TEXT,
    mime_type TEXT,
    embedding_provider TEXT NOT NULL,
    embedding_model TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_content_hash_profile
    ON documents (content_hash, embedding_provider, embedding_model)
    WHERE content_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_documents_source_type ON documents (source_type);
CREATE INDEX IF NOT EXISTS idx_documents_metadata ON documents USING GIN (metadata);

CREATE TABLE IF NOT EXISTS chat_activity_logs (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    username TEXT NOT NULL,
    auth_type TEXT NOT NULL,
    request_path TEXT NOT NULL,
    client_ip TEXT,
    forwarded_for JSONB NOT NULL DEFAULT '[]'::jsonb,
    user_agent TEXT,
    session_id TEXT,
    request_message TEXT NOT NULL,
    response_answer TEXT,
    provider TEXT,
    model TEXT,
    embedding_profile TEXT,
    embedding_provider TEXT,
    embedding_model TEXT,
    used_fallback BOOLEAN NOT NULL DEFAULT FALSE,
    citations_count INTEGER NOT NULL DEFAULT 0,
    retrieved_chunks_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    error_message TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_activity_logs_created_at
    ON chat_activity_logs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_activity_logs_user_id
    ON chat_activity_logs (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_activity_logs_client_ip
    ON chat_activity_logs (client_ip, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_activity_logs_status
    ON chat_activity_logs (status, created_at DESC);

CREATE TABLE IF NOT EXISTS chat_feedback (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    username TEXT NOT NULL,
    session_id TEXT NOT NULL,
    rating SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comments TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_feedback_session_id
    ON chat_feedback (session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_feedback_user_id
    ON chat_feedback (user_id, created_at DESC);
