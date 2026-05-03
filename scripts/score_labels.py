"""
Read a labeled CSV produced by label_conflicts.py and compute precision
plus per-threshold breakdowns. Helps decide whether thresholds are too
loose or too tight.

Usage:
    python scripts/score_labels.py conflicts_to_label.csv
"""
import csv
import sys
from collections import Counter


def bucket(score: float) -> str:
    if score >= 0.50:
        return '[0.50+]'
    if score >= 0.30:
        return '[0.30-0.50)'
    if score >= 0.10:
        return '[0.10-0.30)'
    return '[<0.10]'


def main():
    if len(sys.argv) < 2:
        print('usage: score_labels.py <csv>')
        sys.exit(1)
    path = sys.argv[1]

    rows = []
    with open(path, encoding='utf-8') as f:
        for r in csv.DictReader(f):
            label = (r.get('label') or '').strip().lower()
            if label not in ('yes', 'no', 'unsure'):
                continue
            try:
                r['weighted_score']      = float(r['weighted_score'])
                r['contradiction_score'] = float(r['contradiction_score'])
                r['similarity_score']    = float(r['similarity_score'])
            except (TypeError, ValueError):
                continue
            r['label'] = label
            rows.append(r)

    if not rows:
        print('No labeled rows found. Fill the `label` column with yes/no/unsure.')
        return

    n = len(rows)
    yes = sum(1 for r in rows if r['label'] == 'yes')
    no  = sum(1 for r in rows if r['label'] == 'no')
    uns = sum(1 for r in rows if r['label'] == 'unsure')

    print(f'Labeled rows: {n}')
    print(f'  yes (real contradiction):    {yes}  ({yes/n:.0%})')
    print(f'  no  (false positive):        {no}   ({no/n:.0%})')
    print(f'  unsure:                      {uns}  ({uns/n:.0%})')
    if (yes + no) > 0:
        precision = yes / (yes + no)
        print(f'\nPrecision (excl. unsure): {precision:.0%}')
        print('  >=60% → ship the frontend')
        print('  30-60% → tune thresholds, try mDeBERTa instead of bart-large-mnli')
        print('  <30%  → contradiction signal is mostly noise; rethink')

    print('\nBreakdown by weighted_score bucket:')
    buckets = Counter(bucket(r["weighted_score"]) for r in rows)
    yeses   = Counter(bucket(r["weighted_score"]) for r in rows if r['label'] == 'yes')
    for b in ['[0.50+]', '[0.30-0.50)', '[0.10-0.30)', '[<0.10]']:
        total = buckets.get(b, 0)
        good  = yeses.get(b, 0)
        if total:
            print(f'  {b}: {good}/{total} = {good/total:.0%} precision')

    print('\nBreakdown by contradiction_score bucket:')
    buckets = Counter(bucket(r["contradiction_score"]) for r in rows)
    yeses   = Counter(bucket(r["contradiction_score"]) for r in rows if r['label'] == 'yes')
    for b in ['[0.50+]', '[0.30-0.50)', '[0.10-0.30)', '[<0.10]']:
        total = buckets.get(b, 0)
        good  = yeses.get(b, 0)
        if total:
            print(f'  {b}: {good}/{total} = {good/total:.0%} precision')


if __name__ == '__main__':
    main()
