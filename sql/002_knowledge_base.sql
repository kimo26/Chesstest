-- Phase 3 schema additions: broader knowledge base, game analysis cache.

-- =============================================================
-- Extend rag_documents constraints to allow new source types
-- and chunk levels for the broader knowledge base.
-- =============================================================

-- Drop existing constraints and replace with broader ones.
ALTER TABLE rag_documents
    DROP CONSTRAINT IF EXISTS rag_documents_chunk_level_check;
ALTER TABLE rag_documents
    ADD CONSTRAINT rag_documents_chunk_level_check
    CHECK (chunk_level IN (
        'family', 'variation', 'position', 'theme',
        'strategy', 'endgame', 'middlegame', 'tactics', 'general'
    ));

ALTER TABLE rag_documents
    DROP CONSTRAINT IF EXISTS rag_documents_source_type_check;
-- source_type was a DEFAULT, not a CHECK — add an explicit CHECK now.
-- (If there was no CHECK, this is a no-op on the drop side.)

-- =============================================================
-- Cached per-game Stockfish analysis (for Insights page)
-- =============================================================
CREATE TABLE IF NOT EXISTS game_analyses (
    id              BIGSERIAL PRIMARY KEY,
    user_game_id    BIGINT NOT NULL REFERENCES user_games(id) ON DELETE CASCADE,
    user_id         BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    accuracy        REAL,
    phases          JSONB,
    blunders        INTEGER DEFAULT 0,
    mistakes        INTEGER DEFAULT 0,
    inaccuracies    INTEGER DEFAULT 0,
    analysis_json   TEXT,
    analysed_at     TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_game_id)
);
CREATE INDEX IF NOT EXISTS idx_game_analysis_user ON game_analyses (user_id);
CREATE INDEX IF NOT EXISTS idx_game_analysis_game ON game_analyses (user_game_id);
