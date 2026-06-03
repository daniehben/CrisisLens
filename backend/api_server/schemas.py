from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class SourceSchema(BaseModel):
    source_id: int
    code: str
    name: str
    language: str
    trust_tier: int
    trust_weight: float
    feed_type: str
    is_active: bool

    class Config:
        from_attributes = True


class ArticleSchema(BaseModel):
    article_id: int
    source_code: str
    source_name: str
    url: str
    headline_ar: Optional[str]
    headline_en: Optional[str]
    body_snippet: Optional[str]
    image_url: Optional[str]
    language: str
    trust_weight: float
    published_at: datetime
    fetched_at: datetime

    class Config:
        from_attributes = True


class FeedResponse(BaseModel):
    total: int
    limit: int
    offset: int
    articles: list[ArticleSchema]


class HealthResponse(BaseModel):
    db: str
    articles_count: int
    groq_usage: Optional[dict] = None   # today's Groq request counts per model