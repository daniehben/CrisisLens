-- Independent journalists + YouTube commentary channels.

INSERT INTO sources (name, code, language, trust_tier, trust_weight, feed_url, feed_type, is_active) VALUES
    -- Independent journalists / Substacks
    ('Glenn Greenwald',     'GG',     'en', 4, 0.50, 'https://greenwald.substack.com/feed',            'rss', TRUE),
    ('The Grayzone',        'GZ',     'en', 4, 0.40, 'https://thegrayzone.com/feed/',                  'rss', TRUE),
    ('Caitlin Johnstone',   'CJ',     'en', 5, 0.35, 'https://caitlinjohnstone.substack.com/feed',     'rss', TRUE),
    ('Electronic Intifada', 'EI',     'en', 3, 0.55, 'https://electronicintifada.net/rss.xml',         'rss', TRUE),
    ('Antiwar.com',         'AW',     'en', 4, 0.45, 'https://www.antiwar.com/rss.xml',                'rss', TRUE),
    ('The Cradle',          'CRA',    'en', 4, 0.45, 'https://thecradle.co/feed/',                     'rss', TRUE),
    ('Drop Site News',      'DSN',    'en', 3, 0.55, 'https://www.dropsitenews.com/feed',              'rss', TRUE),
    -- YouTube commentary channels
    ('Breaking Points (YT)',   'YT_BP', 'en', 5, 0.35, 'https://www.youtube.com/feeds/videos.xml?channel_id=UCDRIjKy6eZOvKtOELtTdeUA', 'rss', TRUE),
    ('Democracy Now! (YT)',    'YT_DN', 'en', 4, 0.50, 'https://www.youtube.com/feeds/videos.xml?channel_id=UCzuqE7-t13O4NIDYJfakrhw', 'rss', TRUE),
    ('The Grayzone (YT)',      'YT_GZ', 'en', 5, 0.35, 'https://www.youtube.com/feeds/videos.xml?channel_id=UCEYW0qHEYCsHpkkjwUWxlFw', 'rss', TRUE),
    ('The Real News (YT)',     'YT_RT', 'en', 4, 0.45, 'https://www.youtube.com/feeds/videos.xml?channel_id=UCYwlraEwuFB4ZqASowjoM0g', 'rss', TRUE)
ON CONFLICT (code) DO NOTHING;
