import pytest
from backend.shared.deduplication import get_redis_client, check_and_mark, is_duplicate
from backend.shared.queue import enqueue_article, dequeue_article, queue_size, flush_queue
from backend.shared.models import RawArticle
from datetime import datetime, timezone


@pytest.fixture
def r():
    client = get_redis_client()
    # Clean up test keys before each test
    client.delete("crisislens:seen_urls")
    client.delete("crisislens:article_queue")
    yield client
    client.delete("crisislens:seen_urls")
    client.delete("crisislens:article_queue")


def make_article(url: str) -> RawArticle:
    return RawArticle(
        source_code='TEST',
        external_id='abc123',
        url=url,
        published_at=datetime.now(timezone.utc).replace(tzinfo=None),
        language='en',
        trust_weight=0.80,
        headline_en='Test headline',
        headline_ar=None,
        body_snippet=None,
    )


def test_new_url_not_duplicate(r):
    url = 'https://example.com/article/1'
    assert check_and_mark(r, url) == False  # new → not duplicate


def test_seen_url_is_duplicate(r):
    url = 'https://example.com/article/2'
    check_and_mark(r, url)  # first time → mark it
    assert check_and_mark(r, url) == True  # second time → duplicate


def test_different_urls_not_duplicate(r):
    url1 = 'https://example.com/article/3'
    url2 = 'https://example.com/article/4'
    check_and_mark(r, url1)
    assert check_and_mark(r, url2) == False  # different URL → not duplicate


def test_enqueue_and_dequeue(r):
    article = make_article('https://example.com/article/5')
    enqueue_article(r, article)
    assert queue_size(r) == 1
    result = dequeue_article(r, timeout=1)
    assert result is not None
    assert result.url == article.url
    assert result.headline_en == article.headline_en


def test_queue_fifo_order(r):
    urls = [f'https://example.com/article/{i}' for i in range(3)]
    for url in urls:
        enqueue_article(r, make_article(url))
    # LPUSH + BRPOP = FIFO
    for url in urls:
        result = dequeue_article(r, timeout=1)
        assert result.url == url


def test_dequeue_empty_returns_none(r):
    result = dequeue_article(r, timeout=1)
    assert result is None