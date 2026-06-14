"""Task 9 — Generate sentence embeddings via Jina AI API.

Model: jina-embeddings-v3 (cloud inference, 89 languages, MRL truncation to 384-dim)
  - 384-dim vectors — matches existing vector(384) DB column, no schema migration needed
  - Multilingual: Arabic + English in same embedding space (better than MiniLM-L12)
  - Free tier: 1M tokens/month — sufficient at current scale (~100 articles/cycle)
  - Zero RAM footprint — no local model, no torch, no 480MB download

Why switched from local sentence-transformers:
  - paraphrase-multilingual-MiniLM-L12-v2 requires ~480MB RAM to load
  - Railway Trial plan is 512MB total — OOM SIGKILL on every cycle
  - OOM kills the entire process before APScheduler starts, meaning the
    15-minute interval NEVER fires — only the startup run ever executes
  - Jina API is free, zero memory, and jina-embeddings-v3 outperforms MiniLM-L12

Embedding text strategy (unchanged):
  "<headline>. <summary[:400]>" — topic signal + specific facts
  Arabic preferred over English (platform is Arabic-first).

Requires: JINA_API_KEY env var (free at https://jina.ai — no credit card)
"""
import logging
import os

import requests

from backend.shared.database import get_db_connection

log = logging.getLogger(__name__)

JINA_API_URL  = "https://api.jina.ai/v1/embeddings"
JINA_MODEL    = "jina-embeddings-v3"
EMBEDDING_DIM = 384   # MRL truncation — matches existing vector(384) column


def _build_embed_text(headline: str | None, summary: str | None) -> str | None:
    """
    Combine headline and summary into the text we will embed.

    Priority:
      1. headline + summary  (richest signal)
      2. headline only       (summary not yet generated)
      3. summary only        (rare edge case)
      4. None                (nothing available — skip)
    """
    h = (headline or "").strip()
    s = (summary  or "").strip()
    if h and s:
        return f"{h}. {s[:400]}"
    if h:
        return h
    if s:
        return s[:400]
    return None


def get_embeddings_jina(texts: list[str], batch_size: int = 32) -> list[list[float] | None]:
    """
    Embed a list of texts via Jina AI API.
    Returns one 384-dim vector per input, or None on failure.
    """
    api_key = os.getenv("JINA_API_KEY")
    if not api_key:
        log.error("[Task9] JINA_API_KEY env var not set — skipping embeddings")
        return [None] * len(texts)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    results: list[list[float] | None] = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        try:
            resp = requests.post(
                JINA_API_URL,
                headers=headers,
                json={
                    "model":      JINA_MODEL,
                    "input":      batch,
                    "dimensions": EMBEDDING_DIM,   # MRL truncation to 384
                    "task":       "text-matching", # optimised for semantic similarity
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            # Jina returns items sorted by index — sort defensively anyway
            items = sorted(data["data"], key=lambda x: x["index"])
            results.extend(item["embedding"] for item in items)
        except Exception as e:
            log.warning(f"[Task9] Jina batch {i}–{i+len(batch)} failed: {e} — appending Nones")
            results.extend([None] * len(batch))

    return results


def release_model():
    """
    No-op — kept for scheduler compatibility.
    Local model needed explicit RAM release; Jina API has no memory footprint.
    """
    pass


def run_task9():
    log.info("[Task9] Starting embedding generation (Jina AI API)...")

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    article_id,
                    -- Prefer Arabic fields (platform is Arabic-first)
                    COALESCE(headline_ar, headline_en)   AS headline,
                    COALESCE(summary_ar,  summary)       AS summary
                FROM articles
                WHERE (headline_ar IS NOT NULL OR headline_en IS NOT NULL)
                  AND embedding IS NULL
                ORDER BY article_id
                LIMIT 100
            """)
            rows = cur.fetchall()

    if not rows:
        log.info("[Task9] No articles need embedding.")
        return 0

    article_ids = []
    embed_texts = []
    has_summary = 0

    for article_id, headline, summary in rows:
        text = _build_embed_text(headline, summary)
        if text is None:
            continue
        article_ids.append(article_id)
        embed_texts.append(text)
        if summary:
            has_summary += 1

    if not embed_texts:
        log.info("[Task9] No embeddable text found.")
        return 0

    log.info(
        f"[Task9] Embedding {len(embed_texts)} articles via Jina API "
        f"({has_summary} with summary, {len(embed_texts)-has_summary} headline-only)"
    )

    embeddings = get_embeddings_jina(embed_texts)

    stored = 0
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            for article_id, embedding in zip(article_ids, embeddings):
                if embedding is None:
                    continue
                cur.execute("""
                    UPDATE articles
                    SET embedding     = %s::vector,
                        processed_nlp = TRUE
                    WHERE article_id = %s
                """, (str(embedding), article_id))
                stored += 1
        conn.commit()

    log.info(f"[Task9] Complete — {stored}/{len(embed_texts)} embeddings stored")
    return stored
