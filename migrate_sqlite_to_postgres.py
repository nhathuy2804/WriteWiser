#!/usr/bin/env python3
"""Full v47 migration of local WriteWiser SQLite database to DATABASE_URL PostgreSQL.
Migrates all 12 tables: users (with role), sessions, user_state, user_history (with task & overall_band),
user_notes, app_settings, gate_sessions, global_lessons, library_overrides,
admin_calibrations, feedback_corrections, and reference_overrides.
"""
import os, sqlite3
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


def table_exists(conn, name):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return bool(row)


def table_columns(conn, name):
    if not table_exists(conn, name):
        return set()
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({name})").fetchall()}


counts = {}
with server.db() as dest:
    u_cols = table_columns(source, "users")
    users = source.execute(
        "SELECT id, username, password_hash, salt, created_at"
        + (", role" if "role" in u_cols else ", 'user' AS role")
        + " FROM users ORDER BY id"
    ).fetchall()
    for row in users:
        dest.execute(
            """INSERT INTO users(id, username, password_hash, salt, created_at, role)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT (id) DO UPDATE SET
                 role=excluded.role,
                 password_hash=excluded.password_hash,
                 salt=excluded.salt""",
            (row["id"], row["username"], row["password_hash"], row["salt"], row["created_at"], row["role"] or "user"),
        )
    dest.execute(
        "SELECT setval(pg_get_serial_sequence('users','id'), COALESCE((SELECT MAX(id) FROM users), 1), true)"
    )
    counts["users"] = len(users)

    if table_exists(source, "sessions"):
        rows = source.execute("SELECT token_hash, user_id, expires_at FROM sessions").fetchall()
        for r in rows:
            dest.execute(
                """INSERT INTO sessions(token_hash, user_id, expires_at)
                   VALUES(?,?,?) ON CONFLICT (token_hash) DO NOTHING""",
                (r["token_hash"], r["user_id"], r["expires_at"]),
            )
        counts["sessions"] = len(rows)

    if table_exists(source, "user_state"):
        rows = source.execute("SELECT user_id, state_json, updated_at FROM user_state").fetchall()
        for r in rows:
            dest.execute(
                """INSERT INTO user_state(user_id, state_json, updated_at)
                   VALUES(?,?,?) ON CONFLICT (user_id) DO UPDATE
                   SET state_json=excluded.state_json, updated_at=excluded.updated_at""",
                (r["user_id"], r["state_json"], r["updated_at"]),
            )
        counts["user_state"] = len(rows)

    if table_exists(source, "user_history"):
        uh_cols = table_columns(source, "user_history")
        has_task = "task" in uh_cols and "overall_band" in uh_cols
        rows = source.execute(
            "SELECT user_id, record_id, record_json, created_at, updated_at"
            + (", task, overall_band" if has_task else "")
            + " FROM user_history"
        ).fetchall()
        for r in rows:
            task_val = r["task"] if has_task else None
            band_val = r["overall_band"] if has_task else None
            dest.execute(
                """INSERT INTO user_history(user_id, record_id, record_json, task, overall_band, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT (user_id, record_id) DO UPDATE SET
                     record_json=excluded.record_json,
                     task=COALESCE(excluded.task, user_history.task),
                     overall_band=COALESCE(excluded.overall_band, user_history.overall_band),
                     updated_at=excluded.updated_at""",
                (r["user_id"], r["record_id"], r["record_json"], task_val, band_val, r["created_at"], r["updated_at"]),
            )
        counts["user_history"] = len(rows)

    if table_exists(source, "user_notes"):
        rows = source.execute("SELECT user_id, note_id, note_json, created_at FROM user_notes").fetchall()
        for r in rows:
            dest.execute(
                """INSERT INTO user_notes(user_id, note_id, note_json, created_at)
                   VALUES(?,?,?,?) ON CONFLICT (user_id, note_id) DO UPDATE
                   SET note_json=excluded.note_json""",
                (r["user_id"], r["note_id"], r["note_json"], r["created_at"]),
            )
        counts["user_notes"] = len(rows)

    if table_exists(source, "app_settings"):
        rows = source.execute("SELECT setting_key, setting_value, updated_at FROM app_settings").fetchall()
        for r in rows:
            dest.execute(
                """INSERT INTO app_settings(setting_key, setting_value, updated_at)
                   VALUES(?,?,?) ON CONFLICT (setting_key) DO UPDATE
                   SET setting_value=excluded.setting_value, updated_at=excluded.updated_at""",
                (r["setting_key"], r["setting_value"], r["updated_at"]),
            )
        counts["app_settings"] = len(rows)

    if table_exists(source, "gate_sessions"):
        rows = source.execute("SELECT token_hash, expires_at, created_at FROM gate_sessions").fetchall()
        for r in rows:
            dest.execute(
                """INSERT INTO gate_sessions(token_hash, expires_at, created_at)
                   VALUES(?,?,?) ON CONFLICT (token_hash) DO NOTHING""",
                (r["token_hash"], r["expires_at"], r["created_at"]),
            )
        counts["gate_sessions"] = len(rows)

    if table_exists(source, "global_lessons"):
        rows = source.execute("SELECT id, lesson_json, created_by, created_at, updated_at FROM global_lessons").fetchall()
        for r in rows:
            dest.execute(
                """INSERT INTO global_lessons(id, lesson_json, created_by, created_at, updated_at)
                   VALUES(?,?,?,?,?) ON CONFLICT (id) DO UPDATE
                   SET lesson_json=excluded.lesson_json, updated_at=excluded.updated_at""",
                (r["id"], r["lesson_json"], r["created_by"], r["created_at"], r["updated_at"]),
            )
        counts["global_lessons"] = len(rows)

    if table_exists(source, "library_overrides"):
        rows = source.execute("SELECT id, lesson_json, deleted, updated_at, updated_by FROM library_overrides").fetchall()
        for r in rows:
            dest.execute(
                """INSERT INTO library_overrides(id, lesson_json, deleted, updated_at, updated_by)
                   VALUES(?,?,?,?,?) ON CONFLICT (id) DO UPDATE
                   SET lesson_json=excluded.lesson_json, deleted=excluded.deleted,
                       updated_at=excluded.updated_at, updated_by=excluded.updated_by""",
                (r["id"], r["lesson_json"], r["deleted"], r["updated_at"], r["updated_by"]),
            )
        counts["library_overrides"] = len(rows)

    if table_exists(source, "admin_calibrations"):
        rows = source.execute("SELECT id, payload_json, created_by, created_at, active FROM admin_calibrations").fetchall()
        for r in rows:
            dest.execute(
                """INSERT INTO admin_calibrations(id, payload_json, created_by, created_at, active)
                   VALUES(?,?,?,?,?) ON CONFLICT (id) DO UPDATE
                   SET payload_json=excluded.payload_json, active=excluded.active""",
                (r["id"], r["payload_json"], r["created_by"], r["created_at"], r["active"]),
            )
        counts["admin_calibrations"] = len(rows)

    if table_exists(source, "feedback_corrections"):
        rows = source.execute("SELECT id, user_id, payload_json, status, created_at, reviewed_by FROM feedback_corrections").fetchall()
        for r in rows:
            dest.execute(
                """INSERT INTO feedback_corrections(id, user_id, payload_json, status, created_at, reviewed_by)
                   VALUES(?,?,?,?,?,?) ON CONFLICT (id) DO UPDATE
                   SET payload_json=excluded.payload_json, status=excluded.status, reviewed_by=excluded.reviewed_by""",
                (r["id"], r["user_id"], r["payload_json"], r["status"], r["created_at"], r["reviewed_by"]),
            )
        counts["feedback_corrections"] = len(rows)

    if table_exists(source, "reference_overrides"):
        rows = source.execute("SELECT id, payload_json, deleted, updated_at, updated_by FROM reference_overrides").fetchall()
        for r in rows:
            dest.execute(
                """INSERT INTO reference_overrides(id, payload_json, deleted, updated_at, updated_by)
                   VALUES(?,?,?,?,?) ON CONFLICT (id) DO UPDATE
                   SET payload_json=excluded.payload_json, deleted=excluded.deleted,
                       updated_at=excluded.updated_at, updated_by=excluded.updated_by""",
                (r["id"], r["payload_json"], r["deleted"], r["updated_at"], r["updated_by"]),
            )
        counts["reference_overrides"] = len(rows)

source.close()
print("Migration v47 complete:", ", ".join(f"{k}={v}" for k, v in counts.items()))
print("Review the destination before making the site public.")
