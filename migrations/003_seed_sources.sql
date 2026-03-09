INSERT INTO sources (name, code, language, trust_tier, trust_weight, feed_url, feed_type) VALUES

-- ── Tier 1 ───────────────────────────────────────────────────────────────────
('Al Jazeera Arabic', 'AJA', 'en', 1, 1.00,
 'https://www.aljazeera.com/xml/rss/all.xml',
 'rss'),

-- ── Tier 2 ───────────────────────────────────────────────────────────────────
('Al Jazeera English',      'AJE',  'en', 2, 0.80,
 NULL,
 'newsapi'),

('New York Times',          'NYT',  'en', 2, 0.80,
 NULL,
 'newsapi'),

('BBC News',                'BBC',  'en', 2, 0.80,
 NULL,
 'newsapi'),

('The Jerusalem Post',  'JRP', 'en', 2, 0.75, NULL, 'newsapi'),
('The Washington Post', 'WP',  'en', 2, 0.80, NULL, 'newsapi'),

('Associated Press',        'AP',   'en', 2, 0.80,
 NULL,
 'newsapi'),

-- ── Tier 3 ───────────────────────────────────────────────────────────────────
('Al Arabiya',              'ARB',  'ar', 3, 0.65,
 'https://www.alarabiya.net/tools/rss',
 'mrss'),

('Asharq Al-Awsat', 'ASH', 'ar', 3, 0.65, 'https://aawsat.com/feed', 'rss'),
('The New Arab', 'TNA', 'en', 3, 0.65, 'https://www.newarab.com/rss', 'rss'),
-- ── Tier 4 ───────────────────────────────────────────────────────────────────
('BNO News',                'BNO',  'en', 4, 0.50,
 NULL,
 'telegram'),

('AJ Plus Arabic',          'AJA+', 'ar', 4, 0.50,
 NULL,
 'telegram'),

('Al Jazeera English (TG)', 'AJE+', 'en', 2, 0.80, NULL, 'telegram'),
('Reuters (Telegram)',      'REU',  'en', 2, 0.80, NULL, 'telegram'),
('BBC Breaking (Telegram)', 'BBC+', 'en', 2, 0.80, NULL, 'telegram'),

-- ── Tier 5 ───────────────────────────────────────────────────────────────────
('War Monitor',             'WM',   'en', 5, 0.25,
 NULL,
 'telegram'),

('Spectator Index',         'SI',   'en', 5, 0.10,
 NULL,
 'telegram')

ON CONFLICT (code) DO NOTHING;