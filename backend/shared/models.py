from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class RawArticle:
    """
    Standardised article format returned by all source adapters.
    Every adapter must produce RawArticle objects regardless of source type.
    """
    source_code: str           # e.g. 'AJA', 'NYT', 'BNO'
    external_id: str           # source-assigned ID or MD5 of URL
    url: str                   # canonical article URL
    published_at: datetime     # publication time (UTC)
    language: str              # 'ar' or 'en'
    trust_weight: float        # snapshot from sources table at fetch time

    headline_ar: Optional[str] = None   # Arabic headline
    headline_en: Optional[str] = None   # English headline
    body_snippet: Optional[str] = None  # first 500 chars of body

    def __post_init__(self):
        # Truncate body snippet to 500 chars
        if self.body_snippet and len(self.body_snippet) > 500:
            self.body_snippet = self.body_snippet[:500]

        # At least one headline must exist
        if not self.headline_ar and not self.headline_en:
            raise ValueError(f"Article {self.url} has no headline")