-- LLM-generated content layer.
-- articles.summary       — Groq-cleaned 2-3 sentence summary of body_snippet
-- conflicts.bias_analysis — JSON: per-side claims + factual/framing diff
-- Idempotent.

ALTER TABLE articles
    ADD COLUMN IF NOT EXISTS summary TEXT;

ALTER TABLE conflicts
    ADD COLUMN IF NOT EXISTS bias_analysis JSONB;

-- Partial index so the "needs summary" query stays fast as the corpus grows
CREATE INDEX IF NOT EXISTS idx_articles_needs_summary
    ON articles (article_id)
    WHERE summary IS NULL AND body_snippet IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_conflicts_needs_analysis
    ON conflicts (conflict_id)
    WHERE bias_analysis IS NULL;
