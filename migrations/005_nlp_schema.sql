-- Schema additions required by backend/nlp_pipeline/* tasks 8-12.
-- Idempotent: safe to re-run.

-- ── articles: NLP processing flags ───────────────────────────────────────────
ALTER TABLE articles
    ADD COLUMN IF NOT EXISTS processed_nlp           BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS headline_ar_translated  BOOLEAN NOT NULL DEFAULT FALSE;

-- Index so task8/task9 "find unprocessed" queries don't scan the whole table
CREATE INDEX IF NOT EXISTS idx_articles_processed_nlp
    ON articles (processed_nlp)
    WHERE processed_nlp = FALSE;

-- ── article_pairs: candidate pairs from cosine similarity (task10) ──────────
CREATE TABLE IF NOT EXISTS article_pairs (
    pair_id              BIGSERIAL    PRIMARY KEY,
    article_id_1         BIGINT       NOT NULL REFERENCES articles(article_id) ON DELETE CASCADE,
    article_id_2         BIGINT       NOT NULL REFERENCES articles(article_id) ON DELETE CASCADE,
    similarity_score     NUMERIC(5,4) NOT NULL,
    nli_label            VARCHAR(15)  CHECK (nli_label IN ('contradiction', 'neutral', 'entailment')),
    contradiction_score  NUMERIC(5,4),
    status               VARCHAR(20)  NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending', 'processed', 'error')),
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CHECK (article_id_1 < article_id_2),                  -- enforce canonical ordering
    UNIQUE (article_id_1, article_id_2)
);

CREATE INDEX IF NOT EXISTS idx_article_pairs_status
    ON article_pairs (status)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_article_pairs_article_1
    ON article_pairs (article_id_1);

CREATE INDEX IF NOT EXISTS idx_article_pairs_article_2
    ON article_pairs (article_id_2);
