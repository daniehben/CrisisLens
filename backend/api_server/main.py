from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request
import psycopg2.extras
import redis as redis_lib

from backend.shared.database import get_db_connection
from backend.shared.config import Config
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
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM articles")
                articles_count = cur.fetchone()[0]
                db_status = "ok"
    except Exception as e:
        db_status = f"error: {e}"

    redis_status = "error"
    try:
        redis_url = config.REDIS_URL
        if 'onrender.com' in redis_url or 'render.com' in redis_url:
            redis_url = redis_url.replace('redis://', 'rediss://', 1)
        r = redis_lib.from_url(redis_url, decode_responses=False)
        r.ping()
        redis_status = "ok"
    except Exception as e:
        redis_status = f"error: {e}"

    return HealthResponse(
        db=db_status,
        redis=redis_status,
        articles_count=articles_count,
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