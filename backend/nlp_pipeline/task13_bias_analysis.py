"""Task 13 — Generate per-conflict narrative analysis with Groq.

For each conflict that doesn't have a bias_analysis yet, ask the LLM to
produce a journalist-style analysis with:

  {
    "dispute":             short question capturing the core disagreement
    "narrative":           2–3 sentence flowing analysis (the main report)
    "claims_a":            one sentence — what source A specifically claims
    "claims_b":            one sentence — what source B specifically claims
    "factual_disagreement": concrete facts that differ, or null
    "framing_difference":  vocabulary/framing differences, or null
  }

The "narrative" and "dispute" fields are the primary UX elements.
The other fields back up the modal detail pane.
"""
import json
import logging

import psycopg2.extras

from backend.shared.database import get_db_connection
from backend.shared.groq_client import chat_json, FAST_MODEL, SMART_MODEL

log = logging.getLogger(__name__)

# 5 per cycle: 70B daily cap is 1,000 → plenty of headroom.
# Kept low because 70B has tight RPM and each call ~600 tokens.
BATCH_SIZE = 5

PROMPT = """You are an expert conflict-media analyst. Two news outlets have \
reported on the same event in ways that contradict each other. Your job is to \
write a clear, neutral analysis that a reader can understand WITHOUT reading \
either original article.

Output STRICT JSON with exactly these keys:

- "dispute": A single question (max 12 words) that captures the core \
disagreement. Example: "Who was responsible for the Jabalia camp explosion?" \
or "Did a ceasefire deal get reached?" Make it specific and factual.

- "narrative": A 2–3 sentence journalist-style analysis. Structure it as: \
(1) what the two sources disagree on, (2) what each side specifically claims, \
(3) why the disagreement matters or what explains it (different sourcing, \
framing, political context). Write in flowing prose — no bullet points, no \
labels. Be precise about the factual gap. This is the main text readers see.

- "claims_a": One sentence: what {source_a_name} specifically reports as fact.
- "claims_b": One sentence: what {source_b_name} specifically reports as fact.
- "factual_disagreement": One or two sentences on concrete facts that differ \
(numbers, attributions, timelines), or null if the disagreement is purely \
about framing.
- "framing_difference": One or two sentences on how the same facts are \
described differently (word choice, who is called what, what is omitted), \
or null if minimal.

Rules:
- Be strictly neutral. Describe both perspectives fairly.
- Write in English unless both article bodies are entirely in Arabic.
- Be specific — name the numbers, the actors, the events. Vague analysis \
is useless.
- The "narrative" field should read like a paragraph from a quality newspaper's \
media criticism column.

Source A — {source_a_name} ({source_a_region}):
Headline: {headline_a}
Body: {body_a}

Source B — {source_b_name} ({source_b_region}):
Headline: {headline_b}
Body: {body_b}

Output JSON only. No markdown, no code fences."""


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

                analysis = chat_json(prompt, model=SMART_MODEL, max_tokens=900)
                # If smart model rate-limited or unavailable, fall back to fast.
                if not analysis:
                    analysis = chat_json(prompt, model=FAST_MODEL, max_tokens=900)
                if not analysis or "narrative" not in analysis:
                    log.warning(f"[Task13] Conflict {r['conflict_id']}: no usable analysis from either model")
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
