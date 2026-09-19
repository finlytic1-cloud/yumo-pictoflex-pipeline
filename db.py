"""
Postgres access via the Supabase Session Pooler.

IMPORTANT: use the Session Pooler host (aws-0-{region}.pooler.supabase.com),
not the direct db.{ref}.supabase.co host — the direct host is IPv6-only and
fails to connect from most cloud hosts including Render.
"""
import contextlib

import psycopg2
import psycopg2.extras

import config


def get_conn():
    return psycopg2.connect(
        host=config.DB_HOST,
        port=config.DB_PORT,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        dbname=config.DB_NAME,
        sslmode="require",
        connect_timeout=15,
    )


@contextlib.contextmanager
def cursor(commit: bool = False):
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def fetch_one(query: str, params: tuple = ()) -> dict | None:
    with cursor() as cur:
        cur.execute(query, params)
        return cur.fetchone()


def fetch_all(query: str, params: tuple = ()) -> list[dict]:
    with cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


def execute(query: str, params: tuple = (), returning: bool = False):
    with cursor(commit=True) as cur:
        cur.execute(query, params)
        if returning:
            return cur.fetchone()
        return None
