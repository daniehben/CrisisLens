-- Fix embedding dimension: paraphrase-multilingual-MiniLM-L12-v2 outputs 384-dim
-- vectors, not 768. Schema was wrong.
-- Safe because no rows have embeddings yet (all NULL).

DROP INDEX IF EXISTS idx_articles_embedding;

ALTER TABLE articles
    DROP COLUMN IF EXISTS embedding;

ALTER TABLE articles
    ADD COLUMN embedding vector(384);

CREATE INDEX idx_articles_embedding
    ON articles USING hnsw (embedding vector_cosine_ops)
    WHERE embedding IS NOT NULL;
