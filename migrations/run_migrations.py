import os
import psycopg2
from dotenv import load_dotenv

# Load .env from backend folder
load_dotenv(os.path.join(os.path.dirname(__file__), '..', 'backend', '.env'))

DATABASE_URL = os.getenv('DATABASE_URL')

if not DATABASE_URL:
    raise ValueError("DATABASE_URL not found in environment")

# Fix URL prefix for psycopg2
db_url = DATABASE_URL.replace('postgresql://', 'postgresql://')

MIGRATIONS = [
    '001_create_tables.sql',
    '002_create_indexes.sql',
    '003_seed_sources.sql',
]

def run_migrations():
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    cursor = conn.cursor()

    migrations_dir = os.path.dirname(__file__)

    for filename in MIGRATIONS:
        filepath = os.path.join(migrations_dir, filename)
        print(f"Running {filename}...")
        with open(filepath, 'r') as f:
            sql = f.read()
        try:
            cursor.execute(sql)
            print(f"  ✓ {filename} complete")
        except Exception as e:
            print(f"  ✗ {filename} failed: {e}")
            conn.close()
            raise

    cursor.close()
    conn.close()
    print("\nAll migrations complete.")

if __name__ == '__main__':
    run_migrations()