#!/usr/bin/env python3
"""WriteWiser v47 — Database Layer (SQLite WAL local + Thread-Local Pooled PostgreSQL, Session Pruning & Leaderboard Cache)."""
import os, sqlite3, time, json, hashlib, hmac, secrets, threading, re
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "draftwise.sqlite3"
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
PBKDF2_ROUNDS = 310_000

_PG_LOCAL = threading.local()
_SESSION_PRUNE_LOCK = threading.Lock()
_LAST_SESSION_PRUNE = 0
_LEADERBOARD_LOCK = threading.Lock()
_LEADERBOARD_CACHE = {"data": None, "expires_at": 0}


def invalidate_leaderboard_cache():
    with _LEADERBOARD_LOCK:
        _LEADERBOARD_CACHE["data"] = None
        _LEADERBOARD_CACHE["expires_at"] = 0


def _extract_band_number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            num = float(value)
        else:
            m = re.search(r"(?<!\d)([0-9](?:\.5)?)(?!\d)", str(value))
            if not m:
                return None
            num = float(m.group(1))
        return num if 0.0 <= num <= 9.0 else None
    except (TypeError, ValueError):
        return None


def _extract_record_score_meta(record):
    """Extract (task, overall_band) for independent writing records with valid AI feedback."""
    if not isinstance(record, dict):
        return None, None
    task = record.get("task")
    task_val = task if task in ("Task 1", "Task 2") else None
    if record.get("mode") != "independent":
        return task_val, None
    feedback = record.get("feedback")
    if not isinstance(feedback, dict):
        return task_val, None
    band = _extract_band_number(feedback.get("overall_band"))
    return task_val, band


class SQLiteSession:
    """Small context wrapper so local development uses the same lifecycle as Postgres."""
    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH, timeout=15)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
    def execute(self, sql, params=()):
        return self.conn.execute(sql, params)
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
        finally:
            self.conn.close()
        return False


class PostgresSession:
    """PostgreSQL adapter with thread-local connection reuse to avoid repeated TCP+SSL handshakes per request."""
    MAX_CONN_AGE = 180

    def __init__(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("DATABASE_URL đã được cấu hình nhưng thiếu psycopg. Hãy cài requirements.txt.") from exc
        self._psycopg = psycopg
        self._dict_row = dict_row
        self.conn = self._acquire_conn()

    def _acquire_conn(self):
        now = time.time()
        conn = getattr(_PG_LOCAL, "conn", None)
        created_at = getattr(_PG_LOCAL, "created_at", 0)
        if conn is not None:
            try:
                if not conn.closed and (now - created_at) < self.MAX_CONN_AGE:
                    return conn
                conn.close()
            except Exception:
                pass
        conn = self._psycopg.connect(
            DATABASE_URL,
            connect_timeout=15,
            row_factory=self._dict_row,
            prepare_threshold=None,
        )
        _PG_LOCAL.conn = conn
        _PG_LOCAL.created_at = now
        return conn

    def execute(self, sql, params=()):
        pg_sql = sql.replace("?", "%s")
        try:
            return self.conn.execute(pg_sql, params)
        except self._psycopg.OperationalError:
            try:
                self.conn.close()
            except Exception:
                pass
            _PG_LOCAL.conn = None
            self.conn = self._acquire_conn()
            return self.conn.execute(pg_sql, params)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
        except Exception:
            try:
                self.conn.close()
            except Exception:
                pass
            _PG_LOCAL.conn = None
        return False


def db():
    return PostgresSession() if DATABASE_URL else SQLiteSession()


def is_integrity_error(exc):
    if isinstance(exc, sqlite3.IntegrityError):
        return True
    try:
        import psycopg
        return isinstance(exc, psycopg.IntegrityError)
    except ImportError:
        return False


def maybe_prune_expired_sessions():
    """Periodically clean up expired user sessions and gate sessions."""
    global _LAST_SESSION_PRUNE
    now = int(time.time())
    if now - _LAST_SESSION_PRUNE < 600:
        return
    with _SESSION_PRUNE_LOCK:
        if now - _LAST_SESSION_PRUNE < 600:
            return
        _LAST_SESSION_PRUNE = now
    try:
        with db() as c:
            c.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            c.execute("DELETE FROM gate_sessions WHERE expires_at <= ?", (now,))
    except Exception:
        pass


def _history_created_epoch(record):
    """Keep the original attempt date stable even when its AI feedback is updated."""
    value = record.get("date") if isinstance(record, dict) else None
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            return int(parsed.timestamp())
        except (ValueError, OverflowError, OSError):
            pass
    record_id = record.get("id") if isinstance(record, dict) else None
    try:
        numeric_id = int(record_id)
        if numeric_id > 1_000_000_000_000:
            return numeric_id // 1000
        if numeric_id > 1_000_000_000:
            return numeric_id
    except (TypeError, ValueError, OverflowError):
        pass
    return int(time.time())


def upsert_user_history(conn, user_id, records):
    """Append/update records without letting a stale state snapshot delete older attempts."""
    if not isinstance(records, list):
        return 0
    now = int(time.time())
    saved = 0
    for record in records[:5000]:
        if not isinstance(record, dict) or record.get("id") is None:
            continue
        record_id = str(record.get("id", "")).strip()[:120]
        if not record_id:
            continue
        try:
            row = conn.execute(
                "SELECT record_json, created_at FROM user_history WHERE user_id=? AND record_id=?",
                (user_id, record_id),
            ).fetchone()
            merged = {}
            created_at = _history_created_epoch(record)
            if row:
                try:
                    previous = json.loads(row["record_json"])
                    if isinstance(previous, dict):
                        merged.update(previous)
                    created_at = int(row["created_at"])
                except Exception:
                    pass
            merged.update(record)
            merged["id"] = record.get("id")
            encoded = json.dumps(merged, ensure_ascii=False)
            if len(encoded.encode("utf-8")) > 600_000:
                continue
            task_val, overall_band = _extract_record_score_meta(merged)
            conn.execute(
                """INSERT INTO user_history(user_id, record_id, record_json, task, overall_band, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(user_id, record_id) DO UPDATE SET
                     record_json=excluded.record_json,
                     task=excluded.task,
                     overall_band=excluded.overall_band,
                     updated_at=excluded.updated_at""",
                (user_id, record_id, encoded, task_val, overall_band, created_at, now),
            )
            saved += 1
        except Exception as exc:
            print(f"[history] skip record {record_id[:32]}: {exc}")
    if saved:
        invalidate_leaderboard_cache()
    return saved


def read_user_history(conn, user_id):
    rows = conn.execute(
        "SELECT record_json FROM user_history WHERE user_id=? ORDER BY created_at DESC, updated_at DESC",
        (user_id,),
    ).fetchall()
    records = []
    for row in rows:
        try:
            record = json.loads(row["record_json"])
            if isinstance(record, dict):
                records.append(record)
        except Exception:
            continue
    return records


def init_db():
    if DATABASE_URL:
        with db() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS users (
                id BIGSERIAL PRIMARY KEY,
                username TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                created_at BIGINT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user'
            )""")
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_username_lower_uq ON users (LOWER(username))")
            c.execute("""CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at BIGINT NOT NULL
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS user_state (
                user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                state_json TEXT NOT NULL,
                updated_at BIGINT NOT NULL
            )""")
    else:
        with db() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                role TEXT NOT NULL DEFAULT 'user'
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at INTEGER NOT NULL
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS user_state (
                user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                state_json TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )""")
    with db() as c:
        if DATABASE_URL:
            c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'user'")
        else:
            columns = {row["name"] for row in c.execute("PRAGMA table_info(users)").fetchall()}
            if "role" not in columns:
                c.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
        c.execute("""CREATE TABLE IF NOT EXISTS user_history (
            user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            record_id TEXT NOT NULL,
            record_json TEXT NOT NULL,
            task TEXT,
            overall_band REAL,
            created_at BIGINT NOT NULL,
            updated_at BIGINT NOT NULL,
            PRIMARY KEY (user_id, record_id)
        )""")
        if DATABASE_URL:
            c.execute("ALTER TABLE user_history ADD COLUMN IF NOT EXISTS task TEXT")
            c.execute("ALTER TABLE user_history ADD COLUMN IF NOT EXISTS overall_band DOUBLE PRECISION")
        else:
            uh_cols = {row["name"] for row in c.execute("PRAGMA table_info(user_history)").fetchall()}
            if "task" not in uh_cols:
                c.execute("ALTER TABLE user_history ADD COLUMN task TEXT")
            if "overall_band" not in uh_cols:
                c.execute("ALTER TABLE user_history ADD COLUMN overall_band REAL")
        c.execute("CREATE INDEX IF NOT EXISTS user_history_recent_idx ON user_history (user_id, created_at DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS user_history_leaderboard_idx ON user_history (user_id, task, overall_band)")
        c.execute("""CREATE TABLE IF NOT EXISTS user_notes (
            user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            note_id TEXT NOT NULL,
            note_json TEXT NOT NULL,
            created_at BIGINT NOT NULL,
            PRIMARY KEY (user_id, note_id)
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS user_notes_recent_idx ON user_notes (user_id, created_at DESC)")
        c.execute("""CREATE TABLE IF NOT EXISTS app_settings (
            setting_key TEXT PRIMARY KEY,
            setting_value TEXT NOT NULL,
            updated_at BIGINT NOT NULL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS gate_sessions (
            token_hash TEXT PRIMARY KEY,
            expires_at BIGINT NOT NULL,
            created_at BIGINT NOT NULL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS global_lessons (
            id TEXT PRIMARY KEY,
            lesson_json TEXT NOT NULL,
            created_by TEXT NOT NULL,
            created_at BIGINT NOT NULL,
            updated_at BIGINT NOT NULL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS library_overrides (
            id TEXT PRIMARY KEY,
            lesson_json TEXT,
            deleted INTEGER NOT NULL DEFAULT 0,
            updated_at BIGINT NOT NULL,
            updated_by TEXT NOT NULL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS admin_calibrations (
            id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, created_by TEXT NOT NULL,
            created_at BIGINT NOT NULL, active INTEGER NOT NULL DEFAULT 1
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS feedback_corrections (
            id TEXT PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            payload_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'private',
            created_at BIGINT NOT NULL, reviewed_by TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS reference_overrides (
            id TEXT PRIMARY KEY, payload_json TEXT, deleted INTEGER NOT NULL DEFAULT 0,
            updated_at BIGINT NOT NULL, updated_by TEXT NOT NULL
        )""")
        # Startup migration: migrate any legacy user_state.history once and backfill task/overall_band
        legacy_rows = c.execute('SELECT user_id, state_json FROM user_state WHERE state_json LIKE ?', ('%"history"%',)).fetchall()
        for legacy_row in legacy_rows:
            try:
                old_state = json.loads(legacy_row["state_json"])
                if isinstance(old_state, dict) and "history" in old_state:
                    upsert_user_history(c, legacy_row["user_id"], old_state.pop("history", []))
                    c.execute(
                        "UPDATE user_state SET state_json=?, updated_at=? WHERE user_id=?",
                        (json.dumps(old_state, ensure_ascii=False), int(time.time()), legacy_row["user_id"]),
                    )
            except Exception:
                pass
        unindexed_rows = c.execute(
            'SELECT user_id, record_id, record_json FROM user_history WHERE task IS NULL AND record_json LIKE ?', ('%"independent"%',)
        ).fetchall()
        for r in unindexed_rows:
            try:
                rec = json.loads(r["record_json"])
                t_val, b_val = _extract_record_score_meta(rec)
                if t_val is not None or b_val is not None:
                    c.execute(
                        "UPDATE user_history SET task=?, overall_band=? WHERE user_id=? AND record_id=?",
                        (t_val, b_val, r["user_id"], r["record_id"]),
                    )
            except Exception:
                pass
    ensure_initial_security_accounts()
    maybe_prune_expired_sessions()


def password_hash(password, salt_hex):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), PBKDF2_ROUNDS).hex()


def token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def read_setting(key):
    with db() as c:
        row = c.execute("SELECT setting_value FROM app_settings WHERE setting_key=?", (key,)).fetchone()
    return row["setting_value"] if row else None


def write_setting(key, value):
    with db() as c:
        c.execute(
            """INSERT INTO app_settings(setting_key,setting_value,updated_at) VALUES(?,?,?)
               ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value, updated_at=excluded.updated_at""",
            (key, value, int(time.time())),
        )


def store_password(prefix, password):
    salt = secrets.token_hex(16)
    write_setting(prefix + "_salt", salt)
    write_setting(prefix + "_hash", password_hash(password, salt))


def verify_stored_password(prefix, password):
    salt = read_setting(prefix + "_salt")
    expected = read_setting(prefix + "_hash")
    if not salt or not expected or not isinstance(password, str):
        return False
    return hmac.compare_digest(password_hash(password, salt), expected)


def ensure_initial_security_accounts():
    """Creates a shared gate password and an administrator once, then persists only hashes."""
    generated = []
    with db() as c:
        gate_hash = c.execute("SELECT setting_value FROM app_settings WHERE setting_key='gate_password_hash'").fetchone()
        if not gate_hash:
            gate_password = os.environ.get("WRITEWISER_GATE_PASSWORD", "").strip()
            if not gate_password:
                gate_password = secrets.token_urlsafe(18)
                generated.append(("Mật khẩu truy cập chung", gate_password))
            salt = secrets.token_hex(16)
            c.execute("INSERT INTO app_settings(setting_key,setting_value,updated_at) VALUES(?,?,?)", ("gate_password_salt", salt, int(time.time())))
            c.execute("INSERT INTO app_settings(setting_key,setting_value,updated_at) VALUES(?,?,?)", ("gate_password_hash", password_hash(gate_password, salt), int(time.time())))
        admin = c.execute("SELECT id FROM users WHERE role='admin' LIMIT 1").fetchone()
        if not admin:
            username = os.environ.get("WRITEWISER_ADMIN_USERNAME", "admin").strip() or "admin"
            admin_password = os.environ.get("WRITEWISER_ADMIN_PASSWORD", "")
            if not admin_password:
                admin_password = secrets.token_urlsafe(18)
                generated.append(("Mật khẩu tài khoản admin (username: " + username + ")", admin_password))
            salt = secrets.token_hex(16)
            ph = password_hash(admin_password, salt)
            existing = c.execute("SELECT id FROM users WHERE LOWER(username)=LOWER(?)", (username,)).fetchone()
            if existing:
                c.execute("UPDATE users SET role='admin', password_hash=?, salt=? WHERE id=?", (ph, salt, existing["id"]))
            else:
                c.execute("INSERT INTO users(username,password_hash,salt,created_at,role) VALUES(?,?,?,?,?)", (username, ph, salt, int(time.time()), "admin"))
    if generated:
        print("\n================ WRITEWISER INITIAL CREDENTIALS ================")
        print("Lưu các mật khẩu dưới đây ngay. Server chỉ in chúng trong lần khởi tạo đầu tiên:")
        for label, value in generated:
            print(f"{label}: {value}")
        print("Có thể thay mật khẩu web và mật khẩu admin trong bảng Quản trị sau khi đăng nhập.")
        print("===============================================================\n")


def compute_cached_leaderboard(conn):
    """Fast SQL-aggregated leaderboard with 60s in-memory cache."""
    now = time.time()
    with _LEADERBOARD_LOCK:
        if _LEADERBOARD_CACHE["data"] is not None and now < _LEADERBOARD_CACHE["expires_at"]:
            return _LEADERBOARD_CACHE["data"]
    rows = conn.execute(
        """SELECT users.username AS username,
                  user_history.task AS task,
                  ROUND(AVG(user_history.overall_band), 2) AS avg_overall,
                  COUNT(*) AS submissions
           FROM users
           JOIN user_history ON user_history.user_id = users.id
           WHERE users.role = 'user'
             AND user_history.overall_band IS NOT NULL
             AND user_history.task IN ('Task 1', 'Task 2')
           GROUP BY users.id, users.username, user_history.task"""
    ).fetchall()
    leaderboard = {"Task 1": [], "Task 2": []}
    for row in rows:
        t = row["task"]
        if t in leaderboard and row["submissions"]:
            leaderboard[t].append({
                "username": row["username"],
                "averageOverall": float(row["avg_overall"]) if row["avg_overall"] is not None else None,
                "submissions": int(row["submissions"]),
            })
    for t in ("Task 1", "Task 2"):
        leaderboard[t].sort(key=lambda item: (-(item["averageOverall"] or 0), -item["submissions"], item["username"].lower()))
        for rank, item in enumerate(leaderboard[t], 1):
            item["rank"] = rank
    with _LEADERBOARD_LOCK:
        _LEADERBOARD_CACHE["data"] = leaderboard
        _LEADERBOARD_CACHE["expires_at"] = now + 60
    return leaderboard
