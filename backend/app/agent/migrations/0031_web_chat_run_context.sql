CREATE TABLE IF NOT EXISTS web_chat_run_context (
    run_id TEXT PRIMARY KEY REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    provider TEXT NOT NULL CHECK (provider IN ('chatgpt', 'gemini', 'deepseek', 'zai')),
    page_id INTEGER NOT NULL CHECK (page_id >= 0),
    conversation_url TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
