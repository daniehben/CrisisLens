import psycopg2
from datetime import datetime, timezone
from backend.shared.models import RawArticle
from backend.shared.database import get_db_connection, get_source_map


def write_article(cur, article: RawArticle, source_map: dict) -> bool:
    source_info = source_map.get(article.source_code)
    if not source_info:
        print(f"[db_writer] Unknown source code: {article.source_code}")
        return False

    source_id, _ = source_info

    try:
        cur.execute("""
            INSERT INTO articles (
                source_id, external_id, url, published_at,
                language, trust_weight,
                headline_ar, headline_en, body_snippet
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_id, external_id) DO NOTHING
        """, (
            source_id,
            article.external_id,
            article.url,
            article.published_at,
            article.language,
            article.trust_weight,
            article.headline_ar,
            article.headline_en,
            article.body_snippet,
        ))
        return cur.rowcount == 1

    except Exception as e:
        print(f"[db_writer] Failed to insert {article.url}: {e}")
        return False


def write_batch(articles: list[RawArticle]) -> tuple[int, int]:
    if not articles:
        return 0, 0

    inserted = 0
    skipped = 0
    source_map = get_source_map()

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            for article in articles:
                ok = write_article(cur, article, source_map)
                if ok:
                    inserted += 1
                else:
                    skipped += 1

    return inserted, skipped