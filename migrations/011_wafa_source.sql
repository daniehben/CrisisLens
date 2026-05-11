-- Replace broken MAN (Maan News) with WAFA (Palestinian News Agency)
INSERT INTO sources (name, code, language, trust_tier, trust_weight, feed_url, feed_type, is_active) VALUES
    ('WAFA Palestinian News Agency', 'WAF', 'en', 3, 0.65, 'https://english.wafa.ps/rss', 'rss', TRUE)
ON CONFLICT (code) DO NOTHING;

UPDATE sources SET is_active = FALSE WHERE code = 'MAN';
