"""Task 13 — Generate per-conflict bias analysis with Groq.

For each conflict that doesn't have a bias_analysis yet, ask the LLM to
compare the two sources and return structured JSON:

  {
    "claims_a":            "what source A says",
    "claims_b":            "what source B says",
    "factual_disagreement": "the concrete fact(s) they disagree on" or null,
    "framing_difference":  "how the framing differs" or null
  }

Runs after task12 (so it only sees real, scored conflicts).
"""
import json
import logging

import psycopg2.extras

from backend.shared.database import get_db_connection
from backend.shared.groq_client import chat_json, SMART_MODEL

log = logging.getLogger(__name__)

BATCH_SIZE = 10

PROMPT = """Two news outlets reported on the same event but the headlines \
contradict each other. Compare their coverage and output STRICT JSON with \
these exact keys:

- "claims_a":            one short sentence summarizing source A's claim
- "claims_b":            one short sentence summarizing source B's claim
- "factual_disagreement": one or two sentences naming any concrete factual \
disagreement (numbers, who did what, when, where), or null if there isn't one
- "framing_difference":  one or two sentences on how the same facts are framed \
differently (word choice, emphasis, what's omitted), or null if minimal

Be neutral. Don't take sides. Use the same language the user is reading \
(English unless the bodies are clearly Arabic).

Source A — {source_a_name} ({source_a_region}):
Headline: {headline_a}
Body: {body_a}

Source B — {source_b_name} ({source_b_region}):
Headline: {headline_b}
Body: {body_b}

Output JSON only."""


# Region mapping mirrors the frontend's PERSPECTIVE map; if expanded there,
# update here too.
REGION = {
    "AJA": "Pan-Arab", "AJE": "Pan-Arab", "AJA+": "Pan-Arab", "TNA": "Pan-Arab",
    "ARB": "Gulf", "ASH": "Gulf",
    "BBC": "Western", "AP": "Western", "WP": "Western",
    "DW": "Western (DE)", "F24": "Western (FR)",
    "JRP": "Israeli",
    "MND": "Palestinian", "MAN": "Palestinian", "WAF": "Palestinian",
    "AKH": "Lebanese / Resistance",
    "TAS": "Iranian state", "PTV": "Iranian state",
    "RTA": "Russian state",
    "ANA": "Turkish state",
    "BNO": "Aggregator", "REU": "Western", "BBC+": "Western", "AJE+": "Pan-Arab",
    "AJA+": "Pan-Arab", "MAYE": "Resistance",
    "WM": "OSINT (unverified)", "SI": "Unverified",
    "GG": "Independent journalist", "GZ": "Independent (Grayzone)",
    "CJ": "Independent commentator", "AW": "Independent (antiwar)",
    "CRA": "Independent (Cradle)", "DSN": "Independent (Drop Site)",
    "EI": "Palestinian",
    "YT_BP": "YouTube commentary", "YT_DN": "YouTube (Democracy Now)",
    "YT_GZ": "YouTube (Grayzone)", "YT_RT": "YouTube (Real News)",
}


def run_task13():
    log.info("[Task13] Starting bias analysis...")

    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    c.conflict_id,
                    s1.code AS source_a, s1.name AS source_a_name,
                    s2.code AS source_b, s2.name AS source_b_name,
                    a1.headline_en AS h1_en, a1.headline_ar AS h1_ar,
                    a2.headline_en AS h2_en, a2.headline_ar AS h2_ar,
                    a1.summary AS body_a_sum, a1.body_snippet AS body_a_raw,
                    a2.summary AS body_b_sum, a2.body_snippet AS body_b_raw
                FROM conflicts c
                JOIN articles a1 ON a1.article_id = c.article_a_id
                JOIN articles a2 ON a2.article_id = c.article_b_id
                JOIN sources s1 ON s1.source_id = a1.source_id
                JOIN sources s2 ON s2.source_id = a2.source_id
                WHERE c.bias_analysis IS NULL
                ORDER BY c.weighted_score DESC
                LIMIT %s
            """, (BATCH_SIZE,))
            rows = cur.fetchall()

    if not rows:
        log.info("[Task13] No conflicts need analysis.")
        return 0

    log.info(f"[Task13] Analyzing {len(rows)} conflicts...")

    written = 0
    failed = 0
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            for r in rows:
                # Prefer LLM-cleaned summary; fall back to raw body
                body_a = (r["body_a_sum"] or r["body_a_raw"] or "")[:1500]
                body_b = (r["body_b_sum"] or r["body_b_raw"] or "")[:1500]
                if not body_a or not body_b:
                    failed += 1
                    continue

                prompt = PROMPT.format(
                    source_a_name=r["source_a_name"] or r["source_a"],
                    source_a_region=REGION.get(r["source_a"], "Unaligned"),
                    headline_a=r["h1_en"] or r["h1_ar"] or "",
                    body_a=body_a,
                    source_b_name=r["source_b_name"] or r["source_b"],
                    source_b_region=REGION.get(r["source_b"], "Unaligned"),
                    headline_b=r["h2_en"] or r["h2_ar"] or "",
                    body_b=body_b,
                )

                analysis = chat_json(prompt, model=SMART_MODEL, max_tokens=600)
                if not analysis or "claims_a" not in analysis:
                    failed += 1
                    continue

                cur.execute(
                    "UPDATE conflicts SET bias_analysis = %s WHERE conflict_id = %s",
                    (json.dumps(analysis), r["conflict_id"]),
                )
                written += 1
                log.info(f"[Task13] Conflict {r['conflict_id']} analyzed")
            conn.commit()

    log.info(f"[Task13] Complete — {written} analyzed, {failed} failed")
    return written
