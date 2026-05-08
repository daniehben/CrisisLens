"""Lightweight text heuristics that complement the NLI model.

These are fast string operations — no API calls, no model weights — that
either *boost* a real contradiction (numeric disagreement) or *suppress*
a likely false positive (same-story collision).
"""
import re

# Map Arabic-Indic digits to ASCII so '٧' compares equal to '7'.
_AR_DIGITS = str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789')

# Stopwords to ignore when computing keyword overlap. Mix of EN and AR
# common articles + connectors. Kept short so we don't over-filter.
_STOP = {
    'the','a','an','of','to','in','on','for','and','or','at','by','with','from','as','is','are','was','were','be','this','that','their','his','her','its','it','says','say','said',
    'في','من','على','إلى','عن','مع','أن','إن','هذا','هذه','تلك','ذلك','الذي','التي','وقال','قال','تقول','يقول'
}

_NUM_RE   = re.compile(r'\d+(?:[.,]\d+)?')
_TOKEN_RE = re.compile(r'[A-Za-z؀-ۿ]{3,}')  # latin + arabic, 3+ chars


def extract_numbers(*texts: str) -> set[str]:
    """All numbers across given texts, with Arabic-Indic digits normalized."""
    out = set()
    for t in texts:
        if not t:
            continue
        for m in _NUM_RE.findall(t.translate(_AR_DIGITS)):
            out.add(m.replace(',', '.'))
    return out


def numeric_disagreement(a_texts: list[str], b_texts: list[str]) -> bool:
    """True if both sides have numbers AND no number is shared.
    The classic 'one source says 10 dead, other says 7' case."""
    a, b = extract_numbers(*a_texts), extract_numbers(*b_texts)
    if not a or not b:
        return False
    return a.isdisjoint(b)


def _tokens(text: str) -> set[str]:
    if not text:
        return set()
    return {t.lower() for t in _TOKEN_RE.findall(text) if t.lower() not in _STOP}


def keyword_jaccard(a_texts: list[str], b_texts: list[str]) -> float:
    """Jaccard similarity over content tokens — quick proxy for entity overlap."""
    a = set().union(*(_tokens(t) for t in a_texts))
    b = set().union(*(_tokens(t) for t in b_texts))
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def is_same_story(similarity: float, a_texts: list[str], b_texts: list[str]) -> bool:
    """True if the pair looks like the same event reported by two outlets,
    not a real contradiction. Heuristic: high embedding similarity AND high
    keyword overlap AND no numeric disagreement to argue otherwise."""
    if similarity < 0.85:
        return False
    if numeric_disagreement(a_texts, b_texts):
        return False  # numbers disagree — definitely worth surfacing
    return keyword_jaccard(a_texts, b_texts) >= 0.50
