"""
Export top conflicts to a CSV for manual labeling.

Usage (run from repo root):
    python scripts/label_conflicts.py --limit 50 --out conflicts_to_label.csv

Then open the CSV in Excel/Numbers/Google Sheets and fill the `label`
column with one of: yes, no, unsure
    yes    = the two articles really do contradict each other on a fact
    no     = unrelated, restated, or differently-framed (not contradiction)
    unsure = ambiguous / can't tell from headlines alone

Re-run scripts/score_labels.py afterwards to compute precision.
"""
import argparse
import csv
import os
import sys

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

# Load .env from backend/ relative to repo root
load_dotenv(os.path.join(os.path.dirname(__file__), '..', 'backend', '.env'))


QUERY = """
    SELECT
        c.conflict_id,
        c.weighted_score,
        c.similarity_score,
        c.nli_confidence    AS contradiction_score,
        c.detected_at,
        s1.code             AS source_a,
        s1.trust_weight     AS trust_a,
        a1.headline_en      AS headline_a_en,
        a1.headline_ar      AS headline_a_ar,
        a1.url              AS url_a,
        a1.published_at     AS published_a,
        s2.code             AS source_b,
        s2.trust_weight     AS trust_b,
        a2.headline_en      AS headline_b_en,
        a2.headline_ar      AS headline_b_ar,
        a2.url              AS url_b,
        a2.published_at     AS published_b
    FROM conflicts c
    JOIN articles a1 ON a1.article_id = c.article_a_id
    JOIN articles a2 ON a2.article_id = c.article_b_id
    JOIN sources  s1 ON s1.source_id  = a1.source_id
    JOIN sources  s2 ON s2.source_id  = a2.source_id
    ORDER BY c.weighted_score DESC
    LIMIT %s
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=50)
    ap.add_argument('--out', default='conflicts_to_label.csv')
    args = ap.parse_args()

    db_url = os.getenv('DATABASE_URL')
    if not db_url:
        print('DATABASE_URL not set. Add ?sslmode=require for Render external URL.')
        sys.exit(1)

    with psycopg2.connect(db_url, connect_timeout=10) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(QUERY, (args.limit,))
            rows = cur.fetchall()

    if not rows:
        print('No conflicts in DB yet. Run the worker for a while first.')
        return

    fieldnames = list(rows[0].keys()) + ['label', 'notes']
    with open(args.out, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            r = dict(r)
            r['label'] = ''
            r['notes'] = ''
            w.writerow(r)

    print(f'Wrote {len(rows)} conflicts to {args.out}')
    print('Now fill the `label` column (yes/no/unsure) and run score_labels.py')


if __name__ == '__main__':
    main()
