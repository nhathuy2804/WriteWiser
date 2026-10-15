#!/usr/bin/env python3
"""One-time migration of local Draftwise SQLite accounts/sessions/state to DATABASE_URL Postgres.
Run from this folder with DATABASE_URL set. Prefer an empty Supabase database.
"""
import os, sqlite3, sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
SOURCE = Path(os.environ.get("DRAFTWISE_SQLITE_SOURCE", str(BASE / "draftwise.sqlite3")))
if not os.environ.get("DATABASE_URL", "").strip():
    raise SystemExit("Set DATABASE_URL to the destination PostgreSQL connection string first.")
if not SOURCE.exists():
    raise SystemExit(f"SQLite source not found: {SOURCE}")

import server
server.init_db()
source = sqlite3.connect(SOURCE)
source.row_factory = sqlite3.Row
users = source.execute("SELECT id, username, password_hash, salt, created_at FROM users ORDER BY id").fetchall()
sessions = source.execute("SELECT token_hash, user_id, expires_at FROM sessions").fetchall()
states = source.execute("SELECT user_id, state_json, updated_at FROM user_state").fetchall()

with server.db() as dest:
    for row in users:
        dest.execute("""INSERT INTO users(id, username, password_hash, salt, created_at)
                        VALUES(?,?,?,?,?) ON CONFLICT (id) DO NOTHING""",
                     (row["id"], row["username"], row["password_hash"], row["salt"], row["created_at"]))
    for row in sessions:
        dest.execute("""INSERT INTO sessions(token_hash, user_id, expires_at)
                        VALUES(?,?,?) ON CONFLICT (token_hash) DO NOTHING""",
                     (row["token_hash"], row["user_id"], row["expires_at"]))
    for row in states:
        dest.execute("""INSERT INTO user_state(user_id, state_json, updated_at)
                        VALUES(?,?,?) ON CONFLICT (user_id) DO UPDATE
                        SET state_json=excluded.state_json, updated_at=excluded.updated_at""",
                     (row["user_id"], row["state_json"], row["updated_at"]))
    dest.execute("SELECT setval(pg_get_serial_sequence('users','id'), COALESCE((SELECT MAX(id) FROM users), 1), true)")
source.close()
print(f"Migration complete: {len(users)} users, {len(sessions)} sessions, {len(states)} state records.")
print("Review the destination before making the site public. Re-running skips existing user/session IDs and updates user_state.")
