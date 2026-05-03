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

def _discover_migrations(folder: str) -> list[str]:
    """Auto-discover NNN_*.sql files in numeric order so new migrations
    don't require editing this script."""
    return sorted(
        f for f in os.listdir(folder)
        if f.endswith('.sql') and f[:3].isdigit()
    )

def run_migrations():
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    cursor = conn.cursor()

    migrations_dir = os.path.dirname(__file__)
    migrations = _discover_migrations(migrations_dir)

    for filename in migrations:
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