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


# Opposing vocabulary clusters — each tuple is (group_A_terms, group_B_terms).
# If one side has a term from group_A and the other has a term from group_B
# (or vice versa), that's a framing flip worth surfacing.
_FRAMING_PAIRS: list[tuple[set[str], set[str]]] = [
    # Outcome: dead vs alive
    ({'killed', 'dead', 'died', 'death', 'bodies', 'casualties', 'victims',
      'قتل', 'قتلى', 'استشهد', 'شهيد', 'شهداء', 'ضحايا'},
     {'survived', 'safe', 'alive', 'escaped', 'unhurt', 'rescued',
      'نجا', 'سليم', 'أحياء'}),
    # Actor: terrorist vs fighter
    ({'terrorist', 'terrorists', 'extremist', 'jihadi', 'jihadist',
      'إرهابي', 'إرهابيون', 'متطرف'},
     {'fighter', 'fighters', 'militant', 'resistance', 'combatant', 'freedom fighter',
      'مقاتل', 'مقاومة', 'مجاهد'}),
    # Action: attack vs retaliation
    ({'attack', 'attacked', 'assault', 'bombed', 'shelled',
      'هجوم', 'اعتدى', 'قصف'},
     {'retaliated', 'responded', 'self-defense', 'operation',
      'رد', 'دفاع عن النفس', 'عملية'}),
    # Land framing
    ({'occupied', 'occupation', 'illegal settlements', 'colonists',
      'احتلال', 'محتل', 'مستوطنات'},
     {'disputed territories', 'communities', 'residents',
      'مناطق متنازع عليها', 'سكان'}),
    # Credibility: denied vs confirmed
    ({'denied', 'false', 'fabricated', 'disinformation',
      'نفى', 'كذب', 'مزيف'},
     {'confirmed', 'verified', 'evidence shows', 'witnesses say',
      'أكد', 'تأكد', 'شهود'}),
]

_FRAMING_TOKEN_RE = re.compile(r'[\w؀-ۿ]{3,}', re.UNICODE)


def _framing_tokens(text: str) -> set[str]:
    if not text:
        return set()
    return {t.lower() for t in _FRAMING_TOKEN_RE.findall(text)}


def framing_flip(a_texts: list[str], b_texts: list[str]) -> bool:
    """True if the two sides use opposing vocabulary — 'killed' vs 'martyred',
    'terrorist' vs 'resistance fighter', etc. The core framing differences
    that define cross-perspective conflict."""
    a_tok = set().union(*(_framing_tokens(t) for t in a_texts if t))
    b_tok = set().union(*(_framing_tokens(t) for t in b_texts if t))
    for group_a, group_b in _FRAMING_PAIRS:
        if (a_tok & group_a and b_tok & group_b) or (a_tok & group_b and b_tok & group_a):
            return True
    return False


def is_same_story(similarity: float, a_texts: list[str], b_texts: list[str]) -> bool:
    """True if the pair looks like the same event reported by two outlets,
    not a real contradiction. Heuristic: high embedding similarity AND high
    keyword overlap AND no numeric disagreement to argue otherwise."""
    if similarity < 0.85:
        return False
    if numeric_disagreement(a_texts, b_texts):
        return False  # numbers disagree — definitely worth surfacing
    return keyword_jaccard(a_texts, b_texts) >= 0.50
