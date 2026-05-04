-- Add new Arabic-language RSS sources accessible from Render Frankfurt.
-- Idempotent: ON CONFLICT DO NOTHING for inserts, then explicit updates
-- so existing rows get the right feed_type/url if they were seeded earlier.

INSERT INTO sources (name, code, language, trust_tier, trust_weight, feed_url, feed_type, is_active) VALUES
    ('Deutsche Welle Arabic',  'DW',   'ar', 2, 0.80, 'https://rss.dw.com/rdf/rss-ar-all',          'rss', TRUE),
    ('France 24 Arabic',       'F24',  'ar', 2, 0.80, 'https://www.france24.com/ar/rss',            'rss', TRUE),
    ('Al Arabiya',             'ARB',  'ar', 3, 0.65, 'https://www.alarabiya.net/feed/rss2/ar.xml', 'rss', TRUE)
ON CONFLICT (code) DO NOTHING;

-- If ARB was seeded earlier with feed_type='mrss', flip it to 'rss' so the
-- adapter handles it. No-op if already correct.
UPDATE sources
SET feed_url  = 'https://www.alarabiya.net/feed/rss2/ar.xml',
    feed_type = 'rss',
    is_active = TRUE
WHERE code = 'ARB';
