#!/usr/bin/env python3
"""
CrisisLens — migration runner.

Usage:
    python migrate.py                   # run all pending migrations
    python migrate.py --dry-run         # show what would run, don't execute
    python migrate.py --file 014_new    # run a specific migration file

Uses asyncpg (Python native ssl, not libssl) to avoid Anaconda/macOS OpenSSL issues.
Falls back to psycopg2 if asyncpg is unavailable.
"""
import argparse
import asyncio
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / 'backend' / '.env')

DATABASE_URL = os.getenv('DATABASE_URL', '')


def get_external_url(url: str) -> str:
    """Convert Render internal hostname to external if needed."""
    if not url:
        return url
    if 'render.com' in url or 'localhost' in url or '127.0.0.1' in url:
        return url
    patched = re.sub(
        r'@(dpg-[^/:]+)([/:])' ,
        r'@\1.frankfurt-postgres.render.com\2',
        url
    )
    if 'sslmode' not in patched:
        sep = '&' if '?' in patched else '?'
        patched += sep + 'sslmode=require'
    return patched


def parse_dsn(url: str) -> dict:
    """Parse a postgres:// URL into asyncpg connect kwargs."""
    m = re.match(
        r'postgresql(?:\+\w+)?://([^:]+):([^@]+)@([^/:]+)(?::(\d+))?/([^?]+)(?:\?(.*))?',
        url
    )
    if not m:
        raise ValueError(f"Cannot parse DATABASE_URL: {url}")
    user, password, host, port, dbname, _ = m.groups()
    return {
        'user': user,
        'password': password,
        'host': host,
        'port': int(port or 5432),
        'database': dbname,
    }


async def run_async(pending: list, dry_run: bool):
    import asyncpg
    import ssl as ssl_lib

    url = get_external_url(DATABASE_URL)
    kwargs = parse_dsn(url)

    # Use Python's native ssl module — avoids Anaconda libssl issues
    ssl_ctx = ssl_lib.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl_lib.CERT_NONE

    print("Connecting to database...")
    try:
        conn = await asyncpg.connect(**kwargs, ssl=ssl_ctx)
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        sys.exit(1)

    # Ensure migrations tracking table
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS _migrations (
            filename TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    applied = {r['filename'] for r in await conn.fetch("SELECT filename FROM _migrations")}
    to_run = [f for f in pending if f.name not in applied]

    if not to_run:
        print(f"✅ All {len(pending)} migrations already applied. Nothing to do.")
        await conn.close()
        return

    print(f"Found {len(to_run)} pending migration(s) (of {len(pending)} total):\n")
    for path in to_run:
        sql = path.read_text(encoding='utf-8')
        if dry_run:
            print(f"  [dry-run] {path.name}")
            for line in sql.strip().splitlines()[:6]:
                print(f"    {line}")
            print()
            continue
        try:
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO _migrations (filename) VALUES ($1) ON CONFLICT DO NOTHING",
                    path.name
                )
            print(f"  ✅ {path.name}")
        except Exception as e:
            print(f"  ❌ {path.name} FAILED: {e}")
            await conn.close()
            sys.exit(1)

    await conn.close()
    if not dry_run:
        print(f"\n✅ Done — {len(to_run)} migration(s) applied.")


def run_sync_fallback(pending: list, dry_run: bool):
    """Fallback: psycopg2 (may fail on Anaconda macOS due to SSL)."""
    import psycopg2
    url = get_external_url(DATABASE_URL)
    print("Connecting to database (psycopg2 fallback)...")
    conn = psycopg2.connect(url)
    conn.autocommit = False

    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS _migrations (
                filename TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        conn.commit()
        cur.execute("SELECT filename FROM _migrations")
        applied = {r[0] for r in cur.fetchall()}

    to_run = [f for f in pending if f.name not in applied]
    if not to_run:
        print(f"✅ All {len(pending)} migrations already applied.")
        conn.close()
        return

    print(f"Found {len(to_run)} pending migration(s):\n")
    for path in to_run:
        sql = path.read_text(encoding='utf-8')
        if dry_run:
            print(f"  [dry-run] {path.name}")
            continue
        with conn.cursor() as cur:
            cur.execute(sql)
            cur.execute("INSERT INTO _migrations (filename) VALUES (%s) ON CONFLICT DO NOTHING", (path.name,))
        conn.commit()
        print(f"  ✅ {path.name}")

    conn.close()
    if not dry_run:
        print(f"\n✅ Done — {len(to_run)} migration(s) applied.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--file', help='Run a specific migration file (partial name ok)')
    args = parser.parse_args()

    if not DATABASE_URL:
        print("❌ DATABASE_URL not set.")
        print("   Add it to backend/.env or: export DATABASE_URL='postgresql://...'")
        sys.exit(1)

    migrations_dir = Path(__file__).parent / 'migrations'
    all_files = sorted(migrations_dir.glob('*.sql'))

    if args.file:
        matches = [f for f in all_files if args.file in f.name]
        if not matches:
            print(f"❌ No migration matching '{args.file}'")
            sys.exit(1)
        pending = matches
    else:
        pending = all_files

    # Try asyncpg first (Python native ssl — works on Anaconda macOS)
    try:
        import asyncpg  # noqa
        asyncio.run(run_async(pending, args.dry_run))
    except ImportError:
        print("asyncpg not found — trying psycopg2 fallback...")
        print("(Install asyncpg for better macOS SSL support: pip install asyncpg)\n")
        try:
            run_sync_fallback(pending, args.dry_run)
        except Exception as e:
            print(f"❌ psycopg2 also failed: {e}")
            print()
            print("Both drivers failed. Options:")
            print("  1. pip install asyncpg   (recommended)")
            print("  2. Use Render Shell: dashboard → crisislens-api → Shell → python migrate.py")
            sys.exit(1)


if __name__ == '__main__':
    main()
