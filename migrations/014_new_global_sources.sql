-- New global sources: CNN, Guardian, BBC Arabic, Sky News Arabia,
-- Middle East Eye, Sudan Tribune, Reuters (NewsAPI).
-- Expands coverage beyond Palestine/Iran/Israel cluster.

INSERT INTO sources (name, code, language, trust_tier, trust_weight, feed_url, feed_type, is_active) VALUES
    ('CNN',                 'CNN',  'en', 2, 0.75, 'https://news.google.com/rss/search?q=site:cnn.com&hl=en&gl=US&ceid=US:en',                           'rss',     TRUE),
    ('The Guardian',        'GUA',  'en', 2, 0.78, 'https://www.theguardian.com/world/rss',                                                              'rss',     TRUE),
    ('BBC Arabic',          'BBAR', 'ar', 2, 0.80, 'http://feeds.bbci.co.uk/arabic/rss.xml',                                                             'rss',     TRUE),
    ('Sky News Arabia',     'SKA',  'ar', 3, 0.65, 'https://news.google.com/rss/search?q=site:skynewsarabia.com&hl=ar&gl=AE&ceid=AE:ar',                 'rss',     TRUE),
    ('Middle East Eye',     'MEE',  'en', 3, 0.60, 'https://news.google.com/rss/search?q=site:middleeasteye.net&hl=en&gl=GB&ceid=GB:en',                 'rss',     TRUE),
    ('Sudan Tribune',       'SDT',  'en', 3, 0.60, 'https://sudantribune.com/feed/',                                                                     'rss',     TRUE),
    ('Reuters',             'REU',  'en', 1, 0.85, 'https://newsapi.org/v2/top-headlines?sources=reuters',                                               'newsapi', TRUE)
ON CONFLICT (code) DO UPDATE SET
    name         = EXCLUDED.name,
    trust_weight = EXCLUDED.trust_weight,
    feed_url     = EXCLUDED.feed_url,
    feed_type    = EXCLUDED.feed_type,
    is_active    = TRUE;
