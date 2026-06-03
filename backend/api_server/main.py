from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request
import psycopg2.extras

from backend.shared.database import get_db_connection
from backend.shared.config import Config
from backend.shared.groq_client import get_daily_usage
from backend.api_server.schemas import (
    SourceSchema, ArticleSchema, FeedResponse, HealthResponse
)

config = Config()

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(
    title="CrisisLens API",
    description="Real-time conflict news aggregation with contradiction detection",
    version="0.1.0",
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

@app.on_event("startup")
def run_startup_migrations():
    """Idempotent schema + data migrations — safe to run on every boot."""
    log = __import__('logging').getLogger(__name__)
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # ── Schema columns ──────────────────────────────────────────
                cur.execute("ALTER TABLE articles ADD COLUMN IF NOT EXISTS summary_ar TEXT")
                cur.execute("ALTER TABLE articles ADD COLUMN IF NOT EXISTS image_url TEXT")
                cur.execute("ALTER TABLE articles ADD COLUMN IF NOT EXISTS headline_en_translated BOOLEAN DEFAULT FALSE")
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_articles_needs_summary_ar
                    ON articles (article_id)
                    WHERE summary IS NOT NULL AND summary_ar IS NULL AND language = 'en'
                """)

                # ── Source rows (014/016) — all active sources, idempotent upsert ──
                # ON CONFLICT DO UPDATE keeps name/url/type current on every deploy.
                cur.execute("""
                    INSERT INTO sources (name, code, language, trust_tier, trust_weight, feed_url, feed_type, is_active) VALUES
                        -- Global mainstream (RSS, not NewsAPI)
                        ('BBC News',          'BBC',  'en', 1, 0.80, 'https://feeds.bbci.co.uk/news/world/rss.xml',                                              'rss', TRUE),
                        ('Reuters',           'REU',  'en', 1, 0.85, 'https://news.google.com/rss/search?q=site:reuters.com&hl=en&gl=US&ceid=US:en',             'rss', TRUE),
                        ('Associated Press',  'AP',   'en', 1, 0.80, 'https://feeds.apnews.com/rss/apf-topnews',                                                 'rss', TRUE),
                        ('Washington Post',   'WP',   'en', 2, 0.75, 'https://news.google.com/rss/search?q=site:washingtonpost.com&hl=en&gl=US&ceid=US:en',     'rss', TRUE),
                        ('Jerusalem Post',    'JRP',  'en', 2, 0.70, 'https://news.google.com/rss/search?q=site:jpost.com&hl=en&gl=IL&ceid=IL:en',              'rss', TRUE),
                        ('CNN',               'CNN',  'en', 2, 0.75, 'https://news.google.com/rss/search?q=site:cnn.com&hl=en&gl=US&ceid=US:en',                'rss', TRUE),
                        ('The Guardian',      'GUA',  'en', 2, 0.78, 'https://www.theguardian.com/world/rss',                                                   'rss', TRUE),
                        ('Middle East Eye',   'MEE',  'en', 3, 0.60, 'https://news.google.com/rss/search?q=site:middleeasteye.net&hl=en&gl=GB&ceid=GB:en',     'rss', TRUE),
                        ('Sudan Tribune',     'SDT',  'en', 3, 0.60, 'https://sudantribune.com/feed/',                                                          'rss', TRUE),
                        -- Arabic broadcasters
                        ('BBC Arabic',        'BBAR', 'ar', 2, 0.80, 'http://feeds.bbci.co.uk/arabic/rss.xml',                                                  'rss', TRUE),
                        ('Sky News Arabia',   'SKA',  'ar', 3, 0.65, 'https://news.google.com/rss/search?q=site:skynewsarabia.com&hl=ar&gl=AE&ceid=AE:ar',     'rss', TRUE),
                        -- Breaking / aggregator (RSS, not Telegram)
                        ('BNO News',          'BNO',  'en', 3, 0.50, 'https://bnonews.com/index.php/feed/',                                                     'rss', TRUE),
                        ('Al Mayadeen EN',    'MAYE', 'en', 3, 0.45, 'https://www.almayadeen.net/rss/all.xml',                                                  'rss', TRUE),
                        -- Telegram (only sources with no RSS alternative)
                        ('AJ Plus Arabic',    'AJA+', 'ar', 3, 0.50, 'https://t.me/s/ajplusar',          'telegram_web', TRUE),
                        ('War Monitor',       'WM',   'en', 4, 0.25, 'https://t.me/s/WarMonitor1',       'telegram_web', TRUE),
                        ('Spectator Index',   'SI',   'en', 4, 0.10, 'https://t.me/s/spectatorindex',    'telegram_web', TRUE)
                    ON CONFLICT (code) DO UPDATE SET
                        name         = EXCLUDED.name,
                        trust_weight = EXCLUDED.trust_weight,
                        feed_url     = EXCLUDED.feed_url,
                        feed_type    = EXCLUDED.feed_type,
                        is_active    = TRUE
                """)
                # Disable sources no longer in use
                cur.execute("""
                    UPDATE sources SET is_active = FALSE
                    WHERE code IN ('AJE', 'BBC+')
                """)

            conn.commit()
            log.info("[startup] migrations OK")
    except Exception as e:
        log.warning(f"[startup] migration warning: {e}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.api_route("/", methods=["GET", "HEAD"])
def root():
    return {"status": "ok"}

@app.get("/health", response_model=HealthResponse)
@limiter.limit("60/minute")
def health(request: Request):
    db_status = "error"
    articles_count = 0
    try:
        with get_db_connection() as conn:
            conn.set_session(autocommit=True)
            with conn.cursor() as cur:
                cur.execute("SET statement_timeout = '3000'")
                cur.execute("SELECT COUNT(*) FROM articles")
                articles_count = cur.fetchone()[0]
                db_status = "ok"
    except Exception as e:
        db_status = f"error: {str(e)[:50]}"

    return HealthResponse(
        db=db_status,
        articles_count=articles_count,
        groq_usage=get_daily_usage(),
    )


@app.get("/api/v1/sources", response_model=list[SourceSchema])
@limiter.limit("60/minute")
def get_sources(request: Request):
    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT source_id, code, name, language,
                       trust_tier, trust_weight, feed_type, is_active
                FROM sources
                WHERE is_active = TRUE
                ORDER BY trust_tier, code
            """)
            rows = cur.fetchall()
    return [SourceSchema(**dict(r)) for r in rows]


@app.get("/api/v1/feed", response_model=FeedResponse)
@limiter.limit("60/minute")
def get_feed(
    request: Request,
    language: str = Query(None, description="Filter by language: ar or en"),
    source: str = Query(None, description="Filter by source code e.g. AJA"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    filters = []
    params = []

    if language:
        filters.append("a.language = %s")
        params.append(language)
    if source:
        filters.append("s.code = %s")
        params.append(source)

    where = ("WHERE " + " AND ".join(filters)) if filters else ""

    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Total count
            cur.execute(f"""
                SELECT COUNT(*)
                FROM articles a
                JOIN sources s ON s.source_id = a.source_id
                {where}
            """, params)
            total = cur.fetchone()['count']

            # Paginated results
            cur.execute(f"""
                SELECT
                    a.article_id,
                    s.code AS source_code,
                    s.name AS source_name,
                    a.url,
                    a.headline_ar,
                    a.headline_en,
                    a.body_snippet,
                    a.language,
                    a.trust_weight,
                    a.published_at,
                    a.fetched_at
                FROM articles a
                JOIN sources s ON s.source_id = a.source_id
                {where}
                ORDER BY a.published_at DESC
                LIMIT %s OFFSET %s
            """, params + [limit, offset])
            rows = cur.fetchall()

    articles = [ArticleSchema(**dict(r)) for r in rows]
    return FeedResponse(total=total, limit=limit, offset=offset, articles=articles)
@app.get("/api/v1/conflicts")
@limiter.limit("60/minute")
def get_conflicts(
    request: Request,
    min_score: float = Query(0.0, ge=0.0, le=1.0),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    c.conflict_id,
                    c.conflict_type,
                    c.bias_analysis,
                    c.weighted_score AS conflict_score,
                    c.weighted_score,
                    c.nli_confidence AS contradiction_score,
                    c.similarity_score,
                    c.detected_at,
                    -- side A
                    a1.article_id      AS article_id_1,
                    a1.headline_en     AS headline_1_en,
                    a1.headline_ar     AS headline_1_ar,
                    a1.body_snippet    AS body_1,
                    a1.summary         AS summary_1,
                    COALESCE(a1.summary_ar, CASE WHEN a1.language = 'ar' THEN a1.summary ELSE NULL END) AS summary_1_ar,
                    a1.published_at    AS published_1,
                    a1.url             AS url_a,
                    NULLIF(a1.image_url, '') AS image_url_1,
                    s1.code            AS source_1,
                    s1.name            AS source_1_name,
                    s1.trust_weight    AS trust_score_1,
                    s1.language        AS source_1_lang,
                    -- side B
                    a2.article_id      AS article_id_2,
                    a2.headline_en     AS headline_2_en,
                    a2.headline_ar     AS headline_2_ar,
                    a2.body_snippet    AS body_2,
                    a2.summary         AS summary_2,
                    COALESCE(a2.summary_ar, CASE WHEN a2.language = 'ar' THEN a2.summary ELSE NULL END) AS summary_2_ar,
                    a2.published_at    AS published_2,
                    a2.url             AS url_b,
                    NULLIF(a2.image_url, '') AS image_url_2,
                    s2.code            AS source_2,
                    s2.name            AS source_2_name,
                    s2.trust_weight    AS trust_score_2,
                    s2.language        AS source_2_lang
                FROM conflicts c
                JOIN articles a1 ON a1.article_id = c.article_a_id
                JOIN articles a2 ON a2.article_id = c.article_b_id
                JOIN sources s1 ON s1.source_id = a1.source_id
                JOIN sources s2 ON s2.source_id = a2.source_id
                WHERE c.weighted_score >= %s
                ORDER BY c.weighted_score DESC
                LIMIT %s OFFSET %s
            """, (min_score, limit, offset))
            rows = cur.fetchall()
    return [dict(r) for r in rows]


@app.get("/api/v1/conflicts/{conflict_id}")
@limiter.limit("60/minute")
def get_conflict_detail(request: Request, conflict_id: int):
    """Single conflict with full article context — used by the detail view."""
    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    c.conflict_id,
                    c.conflict_type,
                    c.weighted_score,
                    c.nli_confidence AS contradiction_score,
                    c.similarity_score,
                    c.detected_at,
                    a1.article_id      AS article_id_1,
                    a1.headline_en     AS headline_1_en,
                    a1.headline_ar     AS headline_1_ar,
                    a1.body_snippet    AS body_1,
                    a1.published_at    AS published_1,
                    a1.url             AS url_a,
                    NULLIF(a1.image_url, '') AS image_url_1,
                    s1.code            AS source_1,
                    s1.name            AS source_1_name,
                    s1.trust_weight    AS trust_score_1,
                    s1.language        AS source_1_lang,
                    a2.article_id      AS article_id_2,
                    a2.headline_en     AS headline_2_en,
                    a2.headline_ar     AS headline_2_ar,
                    a2.body_snippet    AS body_2,
                    a2.published_at    AS published_2,
                    a2.url             AS url_b,
                    NULLIF(a2.image_url, '') AS image_url_2,
                    s2.code            AS source_2,
                    s2.name            AS source_2_name,
                    s2.trust_weight    AS trust_score_2,
                    s2.language        AS source_2_lang
                FROM conflicts c
                JOIN articles a1 ON a1.article_id = c.article_a_id
                JOIN articles a2 ON a2.article_id = c.article_b_id
                JOIN sources s1 ON s1.source_id = a1.source_id
                JOIN sources s2 ON s2.source_id = a2.source_id
                WHERE c.conflict_id = %s
            """, (conflict_id,))
            row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Conflict not found")
    return dict(row)
