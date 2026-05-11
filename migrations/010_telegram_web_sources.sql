-- Telegram channel sources via t.me/s/ web scraping (no MTProto needed).
-- Some codes already exist from earlier seeds — ON CONFLICT keeps them.
-- Adds the new ones (MAYE) and ensures all are active.

INSERT INTO sources (name, code, language, trust_tier, trust_weight, feed_url, feed_type, is_active) VALUES
    ('BNO News (Telegram)',              'BNO',  'en', 4, 0.50, 'https://t.me/s/BNOFeed',             'telegram', TRUE),
    ('AJ+ Arabic (Telegram)',            'AJA+', 'ar', 4, 0.50, 'https://t.me/s/ajplusar',            'telegram', TRUE),
    ('Al Jazeera English (Telegram)',    'AJE+', 'en', 2, 0.80, 'https://t.me/s/aje_news',            'telegram', TRUE),
    ('Reuters (Telegram)',               'REU',  'en', 2, 0.80, 'https://t.me/s/reuters_news_agency', 'telegram', TRUE),
    ('BBC Breaking (Telegram)',          'BBC+', 'en', 2, 0.80, 'https://t.me/s/BBCNews_Breaking',    'telegram', TRUE),
    ('Al Mayadeen English (Telegram)',   'MAYE', 'en', 3, 0.45, 'https://t.me/s/AlMayadeenEnglish',   'telegram', TRUE),
    ('War Monitor (Telegram)',           'WM',   'en', 5, 0.25, 'https://t.me/s/WarMonitor1',         'telegram', TRUE),
    ('Spectator Index (Telegram)',       'SI',   'en', 5, 0.10, 'https://t.me/s/spectatorindex',      'telegram', TRUE)
ON CONFLICT (code) DO NOTHING;

-- Refresh existing rows to make sure they're active + have current feed URL
UPDATE sources SET is_active = TRUE, feed_url = 'https://t.me/s/BNOFeed'             WHERE code = 'BNO';
UPDATE sources SET is_active = TRUE, feed_url = 'https://t.me/s/ajplusar'            WHERE code = 'AJA+';
UPDATE sources SET is_active = TRUE, feed_url = 'https://t.me/s/aje_news'            WHERE code = 'AJE+';
UPDATE sources SET is_active = TRUE, feed_url = 'https://t.me/s/reuters_news_agency' WHERE code = 'REU';
UPDATE sources SET is_active = TRUE, feed_url = 'https://t.me/s/BBCNews_Breaking'    WHERE code = 'BBC+';
UPDATE sources SET is_active = TRUE, feed_url = 'https://t.me/s/WarMonitor1'         WHERE code = 'WM';
UPDATE sources SET is_active = TRUE, feed_url = 'https://t.me/s/spectatorindex'      WHERE code = 'SI';
