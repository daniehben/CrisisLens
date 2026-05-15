-- Arabic translation layer for article summaries and bias analysis text fields.
-- articles.summary_ar        — Arabic translation of the English summary
-- bias_analysis fields added inline to the existing JSONB column (no schema change needed)
-- Idempotent.

ALTER TABLE articles
    ADD COLUMN IF NOT EXISTS summary_ar TEXT;

-- Fast lookup: English articles that have a summary but no Arabic translation yet
CREATE INDEX IF NOT EXISTS idx_articles_needs_summary_ar
    ON articles (article_id)
    WHERE summary IS NOT NULL
      AND summary_ar IS NULL
      AND language = 'en';
