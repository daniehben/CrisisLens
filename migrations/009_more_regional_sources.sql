-- Round 2 source expansion: Palestinian voice + State counter-Western + Turkish.
-- Idempotent.

INSERT INTO sources (name, code, language, trust_tier, trust_weight, feed_url, feed_type, is_active) VALUES
    -- Palestinian
    ('Mondoweiss',         'MND', 'en', 3, 0.55, 'https://mondoweiss.net/feed/',                 'rss', TRUE),
    ('Ma''an News Agency', 'MAN', 'en', 3, 0.65, 'https://news.google.com/rss/search?q=site:maannews.net+OR+site:maannews.com&hl=en&gl=PS&ceid=PS:en', 'rss', TRUE),
    ('Al-Akhbar (Lebanon)','AKH', 'ar', 3, 0.55, 'https://news.google.com/rss/search?q=site:al-akhbar.com&hl=ar&gl=LB&ceid=LB:ar', 'rss', TRUE),
    -- State counter-Western
    ('Tasnim News',        'TAS', 'ar', 4, 0.40, 'https://news.google.com/rss/search?q=site:tasnimnews.com/ar&hl=ar&gl=IR&ceid=IR:ar', 'rss', TRUE),
    ('Press TV',           'PTV', 'en', 4, 0.40, 'https://news.google.com/rss/search?q=site:presstv.ir&hl=en&gl=IR&ceid=IR:en', 'rss', TRUE),
    ('RT Arabic',          'RTA', 'ar', 4, 0.35, 'https://news.google.com/rss/search?q=site:arabic.rt.com&hl=ar&gl=RU&ceid=RU:ar', 'rss', TRUE),
    -- Turkish
    ('Anadolu Agency',     'ANA', 'ar', 3, 0.70, 'https://www.aa.com.tr/ar/rss/default?cat=guncel', 'rss', TRUE)
ON CONFLICT (code) DO NOTHING;
