-- Phase 4: persistent coach widget memory + first-run onboarding.
--
-- The coach widget is rendered on every page and needs to recall previous
-- turns so it can answer "why did I blunder on move 14?" without the user
-- re-pasting the position. We extend rag_messages with a JSONB column that
-- captures which page/card/puzzle the user was on when they asked, plus a
-- flag identifying agent-produced answers so the UI can render the
-- tool-call trace.
--
-- The onboarding_state table powers the first-run wizard: on a fresh
-- install the user enters a Chess.com username and the backend runs every
-- data pipeline in a background task while the frontend polls a progress
-- endpoint. One row per user; updated as each step advances.

-- --------------------------------------------------------------
-- rag_messages extensions (idempotent).
-- --------------------------------------------------------------
ALTER TABLE rag_messages ADD COLUMN IF NOT EXISTS context_json JSONB;
ALTER TABLE rag_messages ADD COLUMN IF NOT EXISTS tool_trace   JSONB;
ALTER TABLE rag_messages ADD COLUMN IF NOT EXISTS used_agent   BOOLEAN DEFAULT FALSE;

-- --------------------------------------------------------------
-- Persistent coach conversation per user.
--
-- We add a ``kind`` column so the widget (one rolling conversation per
-- user) can coexist with the full-screen chat page's ad-hoc conversations.
-- --------------------------------------------------------------
ALTER TABLE rag_conversations ADD COLUMN IF NOT EXISTS kind VARCHAR(20) DEFAULT 'chat';
CREATE INDEX IF NOT EXISTS idx_conversations_user_kind
    ON rag_conversations (user_id, kind, created_at DESC);

-- --------------------------------------------------------------
-- Onboarding progress.
-- --------------------------------------------------------------
CREATE TABLE IF NOT EXISTS onboarding_state (
    user_id                 BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    chesscom_username       VARCHAR(50),
    pipelines_started_at    TIMESTAMPTZ,
    pipelines_completed_at  TIMESTAMPTZ,
    current_step            VARCHAR(40),
    steps_done              JSONB NOT NULL DEFAULT '[]'::jsonb,
    last_error              TEXT,
    updated_at              TIMESTAMPTZ DEFAULT NOW()
);
