-- Articles: filter by source and time (main feed query)
CREATE INDEX IF NOT EXISTS idx_articles_source_time
    ON articles (source_id, published_at DESC);

-- Articles: join to events
CREATE INDEX IF NOT EXISTS idx_articles_event
    ON articles (event_id)
    WHERE event_id IS NOT NULL;

-- Articles: pgvector HNSW index for embedding similarity search
-- (only indexes rows that have embeddings, NULL rows skipped)
CREATE INDEX IF NOT EXISTS idx_articles_embedding
    ON articles USING hnsw (embedding vector_cosine_ops)
    WHERE embedding IS NOT NULL;

-- Conflicts: fetch all conflicts for a given event via articles
CREATE INDEX IF NOT EXISTS idx_conflicts_article_a
    ON conflicts (article_a_id);

CREATE INDEX IF NOT EXISTS idx_conflicts_article_b
    ON conflicts (article_b_id);

-- Conflicts: top-N ranking by weighted score
CREATE INDEX IF NOT EXISTS idx_conflicts_score
    ON conflicts (weighted_score DESC);

-- Conflicts: filter unresolved only
CREATE INDEX IF NOT EXISTS idx_conflicts_unresolved
    ON conflicts (is_resolved)
    WHERE is_resolved = FALSE;

-- Ingestion logs: latest run per source
CREATE INDEX IF NOT EXISTS idx_ingestion_logs_source_time
    ON ingestion_logs (source_id, run_at DESC);