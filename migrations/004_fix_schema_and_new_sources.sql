ALTER TABLE articles ADD COLUMN IF NOT EXISTS trust_weight numeric(3,2) NOT NULL DEFAULT 0.50;

INSERT INTO sources (name, code, language, trust_tier, trust_weight, feed_url, feed_type) VALUES
    ('The New Arab',            'TNA',  'en', 3, 0.65, 'https://www.newarab.com/rss', 'rss'),
    ('The Jerusalem Post',      'JRP',  'en', 2, 0.75, NULL,                          'newsapi'),
    ('The Washington Post',     'WP',   'en', 2, 0.80, NULL,                          'newsapi'),
    ('Reuters (Telegram)',      'REU',  'en', 2, 0.80, NULL,                          'telegram'),
    ('BBC Breaking (Telegram)', 'BBC+', 'en', 2, 0.80, NULL,                          'telegram')
ON CONFLICT (code) DO NOTHING;
