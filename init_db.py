#!/usr/bin/env python3
"""One-off script: creates the tables in schema.sql if they don't exist yet.
Run this once before the first Stage 1 run: `python init_db.py`
"""
import db
from utils import log

if __name__ == "__main__":
    with open("schema.sql", "r", encoding="utf-8") as f:
        sql = f.read()
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
        log.info("Schema applied successfully.")
    finally:
        conn.close()
