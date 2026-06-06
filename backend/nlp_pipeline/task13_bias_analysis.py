"""Task 13 — Generate per-conflict framing analysis with Groq.

For each conflict that doesn't have a framing_analysis yet, ask the LLM to
produce a journalist-style analysis with:

  {
    "dispute":             short question capturing the core disagreement
    "narrative":           2–3 sentence flowing analysis (the main report)
    "claims_a":            one sentence — what source A specifically claims
    "claims_b":            one sentence — what source B specifically claims
    "key_question":        the single question a journalist would verify to resolve this
    "factual_disagreement": concrete facts that differ, or null
    "framing_difference":  vocabulary/framing differences, or null
  }

Improvements over v1:
  - Passes NLI verdict (contradiction/neutral/entailment + confidence) so the
    LLM understands the machine's confidence level before writing
  - Passes trust scores for both sources so editorial context is accurate
  - Passes conflict_type so the LLM knows if this is a factual or framing clash
  - Adds key_question field: the journalist's verification question
  - Source editorial profiles added to REGION map (not just region label)
"""
import json
import logging

import psycopg2.extras

from backend.shared.database import get_db_connection
from backend.shared.groq_client import chat_json, FAST_MODEL, SMART_MODEL

log = logging.getLogger(__name__)

BATCH_SIZE = 5

# Source editorial profile: one-line description used in the LLM prompt
# for editorial context. Not shown to users.
SOURCE_PROFILE = {
    "AJA":  "Al Jazeera — Qatari state-funded, Pan-Arab editorial line, trust 1.00",
    "AJA+": "AJ Plus Arabic — Al Jazeera digital, youth-oriented, Pan-Arab, trust 0.50",
    "ARB":  "Al Arabiya — Saudi-owned, Gulf editorial line, trust 0.65",
    "ASH":  "Asharq Al-Awsat — Saudi-owned, London-based, trust 0.60",
    "BBC":  "BBC News — UK public broadcaster, editorially independent, trust 0.80",
    "BBAR": "BBC Arabic — BBC World Service Arabic, trust 0.80",
    "AP":   "Associated Press — US wire service, factual reporting standard, trust 0.80",
    "REU":  "Reuters — UK-based wire service, factual reporting standard, trust 0.85",
    "WP":   "Washington Post — US liberal broadsheet, trust 0.75",
    "CNN":  "CNN — US cable news, centre-left editorial line, trust 0.75",
    "GUA":  "The Guardian — UK left-liberal broadsheet, trust 0.78",
    "DW":   "Deutsche Welle Arabic — German public broadcaster, Arabic service, trust 0.80",
    "F24":  "France 24 Arabic — French public broadcaster, Arabic service, trust 0.80",
    "JRP":  "Jerusalem Post — Israeli centre-right broadsheet, trust 0.70",
    "SKA":  "Sky News Arabia — UAE/Saudi joint venture, Gulf editorial line, trust 0.65",
    "ANA":  "Anadolu Agency — Turkish state news agency, trust 0.70",
    "MEE":  "Middle East Eye — UK-based independent, pro-Palestinian editorial line, trust 0.60",
    "MND":  "Mondoweiss — US-based, explicitly pro-Palestinian, trust 0.55",
    "WAF":  "WAFA — Palestinian Authority official news agency, trust 0.65",
    "AKH":  "Al-Akhbar Lebanon — Lebanese left-wing, Hezbollah-aligned, trust 0.55",
    "EI":   "Electronic Intifada — US-based, explicitly pro-Palestinian, trust 0.55",
    "TAS":  "Tasnim — Iranian state news agency, trust 0.40",
    "PTV":  "Press TV — Iranian state broadcaster, trust 0.40",
    "RTA":  "RT Arabic — Russian state media, trust 0.35",
    "GG":   "Glenn Greenwald — Independent journalist, anti-establishment, trust 0.50",
    "GZ":   "The Grayzone — US-based, anti-NATO editorial line, trust 0.40",
    "CJ":   "Caitlin Johnstone — Australian independent commentator, anti-war, trust 0.35",
    "AW":   "Antiwar.com — US anti-interventionist, trust 0.45",
    "CRA":  "The Cradle — Lebanon-based, resistance-axis editorial line, trust 0.45",
    "DSN":  "Drop Site News — US investigative, independent, trust 0.55",
    "BNO":  "BNO News — Breaking news aggregator, trust 0.50",
    "MAYE": "Al Mayadeen EN — Lebanon-based, resistance-axis, trust 0.45",
    "SDT":  "Sudan Tribune — Independent, Africa-focused, trust 0.60",
    "WM":   "War Monitor — OSINT Telegram channel, unverified, trust 0.25",
    "SI":   "Spectator Index — Breaking news Telegram, unverified, trust 0.10",
    "YT_BP":"Breaking Points — US independent political commentary, trust 0.35",
    "YT_DN":"Democracy Now! — US progressive public media, trust 0.50",
    "YT_RT":"The Real News Network — US progressive independent, trust 0.45",
}

PROMPT = """You are an expert conflict-media analyst. Two news outlets have \
reported on the same event in ways that diverge. Your job is to write a clear, \
neutral analysis that a reader can understand WITHOUT reading either original article.

Machine analysis context (for your reference — do not quote these numbers directly):
- Similarity score: {similarity_score:.2f} (how related the articles are, 0–1; higher = same event)
- Contradiction model verdict: {nli_label} (confidence: {nli_confidence:.2f})
- Divergence type detected: {conflict_type}
- Source A editorial profile: {source_a_profile}
- Source B editorial profile: {source_b_profile}

Output STRICT JSON with exactly these keys:

- "dispute": A single question (max 12 words) that captures the core \
disagreement. Example: "Who was responsible for the Jabalia camp explosion?" \
or "Did a ceasefire deal get reached?" Make it specific and factual.

- "narrative": A 2–3 sentence journalist-style analysis. Structure it as: \
(1) what the two sources disagree on, (2) what each side specifically claims, \
(3) why the disagreement matters or what explains it — consider the editorial \
traditions, regional positions, and funding of each source when relevant. \
Write in flowing prose — no bullet points, no labels. Be precise about the \
factual gap. This is the main text readers see.

- "claims_a": One sentence: what {source_a_name} specifically reports as fact.
- "claims_b": One sentence: what {source_b_name} specifically reports as fact.

- "key_question": The single most important question a journalist would need \
to verify to resolve this contradiction. Max 15 words. Example: \
"Were the strikes targeting military infrastructure or civilian buildings?" \
or "What is the confirmed casualty count from independent sources?"

- "factual_disagreement": One or two sentences on concrete facts that differ \
(numbers, attributions, timelines, identities), or null if the disagreement \
is purely about framing.

- "framing_difference": One or two sentences on how the same facts are \
described differently (word choice, who is called what, what is omitted, \
active vs passive voice), or null if minimal.

- "emotion_a": An object with exactly these 5 keys scoring the emotional \
register of {source_a_name}'s article on a 0.0–1.0 scale: \
"anger" (outrage, condemnation, accusations), \
"fear" (threat, danger, alarm), \
"sadness" (grief, loss, mourning), \
"hope" (resolution, progress, optimism), \
"neutral" (dry factual reporting, no emotional charge). \
Scores do not need to sum to 1. Each is independent. Example: \
{{"anger": 0.7, "fear": 0.3, "sadness": 0.5, "hope": 0.1, "neutral": 0.2}}

- "emotion_b": Same structure as emotion_a but for {source_b_name}'s article.

Rules:
- Be strictly neutral. Describe both perspectives fairly.
- Write in English unless both article bodies are entirely in Arabic.
- Be specific — name the numbers, the actors, the events. Vague analysis \
is useless.
- The "narrative" field should read like a paragraph from a quality newspaper's \
media criticism column.
- Do not invent facts. If a source does not say something, do not attribute it.

Source A — {source_a_name}:
Headline: {headline_a}
Body: {body_a}

Source B — {source_b_name}:
Headline: {headline_b}
Body: {body_b}

Output JSON only. No markdown, no code fences."""


def run_task13():
    log.info("[Task13] Starting framing analysis...")

    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    c.conflict_id,
                    c.conflict_type,
                    c.nli_confidence,
                    c.similarity_score,
                    c.nli_label,
                    s1.code AS source_a, s1.name AS source_a_name,
                    s1.trust_weight AS trust_a,
                    s2.code AS source_b, s2.name AS source_b_name,
                    s2.trust_weight AS trust_b,
                    a1.headline_en AS h1_en, a1.headline_ar AS h1_ar,
                    a2.headline_en AS h2_en, a2.headline_ar AS h2_ar,
                    a1.summary AS body_a_sum, a1.body_snippet AS body_a_raw,
                    a2.summary AS body_b_sum, a2.body_snippet AS body_b_raw
                FROM conflicts c
                JOIN articles a1 ON a1.article_id = c.article_a_id
                JOIN articles a2 ON a2.article_id = c.article_b_id
                JOIN sources s1 ON s1.source_id = a1.source_id
                JOIN sources s2 ON s2.source_id = a2.source_id
                WHERE (
                    c.framing_analysis IS NULL
                    OR (c.framing_analysis->>'narrative') IS NULL
                )
                ORDER BY c.weighted_score DESC
                LIMIT %s
            """, (BATCH_SIZE,))
            rows = cur.fetchall()

    if not rows:
        log.info("[Task13] No conflicts need framing analysis.")
        return 0

    log.info(f"[Task13] Analyzing {len(rows)} conflicts...")

    written = 0
    failed = 0
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            for r in rows:
                body_a = (r["body_a_sum"] or r["body_a_raw"] or "")[:1500]
                body_b = (r["body_b_sum"] or r["body_b_raw"] or "")[:1500]
                if not body_a or not body_b:
                    failed += 1
                    continue

                source_a_profile = SOURCE_PROFILE.get(
                    r["source_a"],
                    f"{r['source_a_name']} — trust {r['trust_a']:.2f}"
                )
                source_b_profile = SOURCE_PROFILE.get(
                    r["source_b"],
                    f"{r['source_b_name']} — trust {r['trust_b']:.2f}"
                )

                prompt = PROMPT.format(
                    similarity_score=float(r["similarity_score"] or 0),
                    nli_label=r["nli_label"] or "unknown",
                    nli_confidence=float(r["nli_confidence"] or 0),
                    conflict_type=r["conflict_type"] or "unknown",
                    source_a_name=r["source_a_name"] or r["source_a"],
                    source_a_profile=source_a_profile,
                    headline_a=r["h1_en"] or r["h1_ar"] or "",
                    body_a=body_a,
                    source_b_name=r["source_b_name"] or r["source_b"],
                    source_b_profile=source_b_profile,
                    headline_b=r["h2_en"] or r["h2_ar"] or "",
                    body_b=body_b,
                )

                analysis = chat_json(prompt, model=SMART_MODEL, max_tokens=1200)
                if not analysis:
                    analysis = chat_json(prompt, model=FAST_MODEL, max_tokens=1200)
                if not analysis or "narrative" not in analysis:
                    log.warning(f"[Task13] Conflict {r['conflict_id']}: no usable analysis from either model")
                    failed += 1
                    continue

                cur.execute(
                    "UPDATE conflicts SET framing_analysis = %s WHERE conflict_id = %s",
                    (json.dumps(analysis), r["conflict_id"]),
                )
                written += 1
                log.info(f"[Task13] Conflict {r['conflict_id']} analyzed")
            conn.commit()

    log.info(f"[Task13] Complete — {written} analyzed, {failed} failed")
    return written
