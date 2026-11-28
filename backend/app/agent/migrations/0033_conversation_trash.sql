ALTER TABLE conversations ADD COLUMN deleted_at TEXT;

CREATE INDEX IF NOT EXISTS idx_conversations_deleted_activity
ON conversations(deleted_at, updated_at DESC, id DESC);
