-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- ── sources ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sources (
    source_id     SERIAL PRIMARY KEY,
    name          VARCHAR(100) NOT NULL,
    code          VARCHAR(20)  NOT NULL UNIQUE,
    language      CHAR(2)      NOT NULL,
    trust_tier    SMALLINT     NOT NULL CHECK (trust_tier BETWEEN 1 AND 5),
    trust_weight  NUMERIC(3,2) NOT NULL CHECK (trust_weight BETWEEN 0.00 AND 1.00),
    feed_url      TEXT,
    feed_type     VARCHAR(20)  NOT NULL CHECK (feed_type IN ('rss', 'newsapi', 'telegram', 'mrss')),
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ── events ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS events (
    event_id      SERIAL PRIMARY KEY,
    title         TEXT         NOT NULL,
    location      VARCHAR(200),
    started_at    TIMESTAMPTZ  NOT NULL,
    last_activity TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    status        VARCHAR(20)  NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active', 'resolved', 'stale')),
    article_count INT          NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ── articles ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS articles (
    article_id    BIGSERIAL PRIMARY KEY,
    source_id     INT          NOT NULL REFERENCES sources(source_id),
    event_id      INT          REFERENCES events(event_id),
    external_id   VARCHAR(255) NOT NULL,
    headline_ar   TEXT,
    headline_en   TEXT,
    body_snippet  TEXT,
    published_at  TIMESTAMPTZ  NOT NULL,
    fetched_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    url           TEXT         NOT NULL UNIQUE,
    language      CHAR(2)      NOT NULL,
    embedding     vector(768),
    trust_weight  NUMERIC(3,2) NOT NULL,
    UNIQUE (source_id, external_id)
);

-- ── conflicts ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS conflicts (
    conflict_id      BIGSERIAL PRIMARY KEY,
    article_a_id     BIGINT       NOT NULL REFERENCES articles(article_id),
    article_b_id     BIGINT       NOT NULL REFERENCES articles(article_id),
    conflict_type    VARCHAR(30)  NOT NULL,
    similarity_score NUMERIC(5,4) NOT NULL,
    nli_label        VARCHAR(15)  NOT NULL CHECK (nli_label IN ('contradiction', 'neutral', 'entailment')),
    nli_confidence   NUMERIC(5,4) NOT NULL,
    weighted_score   NUMERIC(5,4) NOT NULL,
    detected_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    is_resolved      BOOLEAN      NOT NULL DEFAULT FALSE,
    CHECK (article_a_id <> article_b_id),
    UNIQUE (article_a_id, article_b_id)
);

-- ── ingestion_logs ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ingestion_logs (
    log_id           BIGSERIAL PRIMARY KEY,
    source_id        INT         NOT NULL REFERENCES sources(source_id),
    run_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    articles_fetched INT         NOT NULL DEFAULT 0,
    articles_new     INT         NOT NULL DEFAULT 0,
    articles_duped   INT         NOT NULL DEFAULT 0,
    duration_ms      INT,
    status           VARCHAR(20) NOT NULL DEFAULT 'ok'
                     CHECK (status IN ('ok', 'error', 'partial')),
    error_message    TEXT
);