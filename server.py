#!/usr/bin/env python3
"""WriteWiser backend: local SQLite for development, PostgreSQL via DATABASE_URL for cloud hosting."""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse
from http import cookies
import hashlib, hmac, json, os, secrets, sqlite3, time, re, urllib.request, urllib.error, base64, io, zipfile, csv, xml.etree.ElementTree as ET, html
from pathlib import Path
from html.parser import HTMLParser
import threading

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "draftwise.sqlite3"
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
HOST = os.environ.get("DRAFTWISE_HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))
COOKIE_NAME = "draftwise_session"
SESSION_TTL = 60 * 60 * 24 * 30
PBKDF2_ROUNDS = 310_000
USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{3,24}$")
MAX_BODY = 4_000_000
_RATE_LOCK = threading.Lock()
_RATE_BUCKETS = {}

class SQLiteSession:
    """Small context wrapper so local development uses the same lifecycle as Postgres."""
    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
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
    """PostgreSQL adapter; SQL uses ? placeholders internally for SQLite compatibility."""
    def __init__(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("DATABASE_URL đã được cấu hình nhưng thiếu psycopg. Hãy cài requirements.txt.") from exc
        self.conn = psycopg.connect(DATABASE_URL, connect_timeout=15, row_factory=dict_row, prepare_threshold=None)
    def execute(self, sql, params=()):
        # All SQL statements in this project use ? only as positional placeholders.
        return self.conn.execute(sql.replace("?", "%s"), params)
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
    # Backward-compatible migration for databases created by earlier v13 builds.
    with db() as c:
        if DATABASE_URL:
            c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'user'")
        else:
            columns = {row["name"] for row in c.execute("PRAGMA table_info(users)").fetchall()}
            if "role" not in columns:
                c.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
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
    ensure_initial_security_accounts()

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
        c.execute("""INSERT INTO app_settings(setting_key,setting_value,updated_at) VALUES(?,?,?)
                     ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value, updated_at=excluded.updated_at""",
                  (key, value, int(time.time())))

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

class DolLinkParser(HTMLParser):
    """Extract public article titles and URLs only; never downloads article bodies."""
    def __init__(self, base_url):
        super().__init__()
        self.base_url = base_url
        self.items = []
        self.current = None
        self.parts = []
    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        attrs = dict(attrs)
        href = attrs.get("href", "")
        if "/blog/" in href and "tuhoc.dolenglish.vn" in self.base_url:
            from urllib.parse import urljoin, urlparse
            url = urljoin(self.base_url, href)
            parsed = urlparse(url)
            if parsed.scheme == "https" and parsed.netloc in ("tuhoc.dolenglish.vn", "www.dolenglish.vn", "dolacademy.vn", "www.dolacademy.vn"):
                self.current = {"url": url, "title": ""}
                self.parts = []
    def handle_data(self, data):
        if self.current is not None:
            text = " ".join(data.split())
            if text:
                self.parts.append(text)
    def handle_endtag(self, tag):
        if tag == "a" and self.current is not None:
            title = " ".join(self.parts).strip()
            if 12 <= len(title) <= 240 and not any(x["url"] == self.current["url"] for x in self.items):
                self.current["title"] = title
                self.items.append(self.current)
            self.current = None
            self.parts = []


def fetch_dol_catalog(task):
    """Fetch listing pages for article metadata only, on explicit user request."""
    from urllib.parse import urlparse
    sources = []
    if task in ("all", "Task 1"):
        sources.append(("Task 1", "https://tuhoc.dolenglish.vn/category/tong-hop-bai-mau-ielts-writing-task-1-sample"))
    if task in ("all", "Task 2"):
        sources.append(("Task 2", "https://tuhoc.dolenglish.vn/category/tong-hop-bai-mau-ielts-writing-task-2-sample"))
    found = []
    for label, url in sources:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; WriteWiserPersonalStudy/1.0)", "Accept": "text/html"})
        with urllib.request.urlopen(req, timeout=12) as response:
            html = response.read(2_500_000).decode("utf-8", "replace")
        parser = DolLinkParser(url)
        parser.feed(html)
        for item in parser.items:
            if item["url"].rstrip("/") == url.rstrip("/"):
                continue
            found.append({"task": label, "title": item["title"], "url": item["url"], "description": "Bài viết từ kho bài mẫu công khai của DOL. Mở trang gốc để xem nội dung đầy đủ."})
    seen = set()
    output = []
    for item in found:
        if item["url"] not in seen:
            seen.add(item["url"])
            output.append(item)
    return output[:120]


class TextOnlyParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts=[]; self.skip=0
    def handle_starttag(self, tag, attrs):
        if tag.lower() in ("script", "style", "noscript", "svg"):
            self.skip += 1
        if tag.lower() in ("p", "div", "br", "li", "h1", "h2", "h3", "tr", "section") and not self.skip:
            self.parts.append("\n")
    def handle_endtag(self, tag):
        if tag.lower() in ("script", "style", "noscript", "svg") and self.skip:
            self.skip -= 1
        if tag.lower() in ("p", "div", "li", "h1", "h2", "h3", "tr", "section") and not self.skip:
            self.parts.append("\n")
    def handle_data(self, data):
        if not self.skip and data.strip(): self.parts.append(data.strip())

def extract_docx_text(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        xml_data = archive.read("word/document.xml")
    root = ET.fromstring(xml_data)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs=[]
    for para in root.findall(".//w:p", ns):
        text="".join(node.text or "" for node in para.findall(".//w:t", ns)).strip()
        if text: paragraphs.append(text)
    return "\n\n".join(paragraphs)

def infer_import_sections(text, filename):
    # Preserve empty lines because they encode paragraph boundaries for reverse translation.
    lines=[line.strip() for line in str(text).replace("\r", "").split("\n")]
    while lines and not lines[0]: lines.pop(0)
    while lines and not lines[-1]: lines.pop()
    title=Path(filename).stem[:180] if filename else "Bài nhập mới"
    question=""
    body_lines=[]
    mode="body"
    question_lines=[]
    essay_lines=[]
    saw_question=False
    saw_essay=False
    q_re=re.compile(r"^(?:question|prompt|đề bài|de bai|writing task\s*[12])\s*[:：-]?\s*(.*)$",re.I)
    essay_re=re.compile(r"^(?:sample answer|model answer|sample essay|model essay|sample response|bài mẫu|bai mau|bài viết mẫu|essay|model response)\s*[:：-]?\s*(.*)$",re.I)
    title_re=re.compile(r"^(?:title|tiêu đề|tieu de)\s*[:：-]\s*(.+)$",re.I)
    for line in lines:
        tm=title_re.match(line)
        if tm: title=tm.group(1).strip()[:180]; continue
        qm=q_re.match(line)
        if qm:
            saw_question=True; mode="question"
            if qm.group(1).strip(): question_lines.append(qm.group(1).strip())
            continue
        em=essay_re.match(line)
        if em:
            saw_essay=True; mode="essay"
            if em.group(1).strip(): essay_lines.append(em.group(1).strip())
            continue
        if not line:
            target=question_lines if mode=="question" else essay_lines if mode=="essay" else body_lines
            if target and target[-1] != "": target.append("")
            continue
        if mode=="question": question_lines.append(line)
        elif mode=="essay": essay_lines.append(line)
        else: body_lines.append(line)
    if saw_question and saw_essay:
        question=" ".join(x for x in question_lines if x).strip()
        body="\n".join(essay_lines).strip()
    else:
        question=""
        body="\n".join(body_lines or lines).strip()
    return {"title":title,"question":question,"english":body}

def parse_import_file(data):
    filename = str(data.get("filename", "import.txt"))[:180]
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else "txt"
    if suffix == "docx":
        encoded = data.get("base64", "")
        if not isinstance(encoded, str) or len(encoded) > 3_000_000:
            raise ValueError("Tệp DOCX không hợp lệ hoặc quá lớn.")
        try: raw = base64.b64decode(encoded, validate=True); text = extract_docx_text(raw)
        except Exception as e: raise ValueError("Không đọc được DOCX. Hãy kiểm tra tệp có phải Word .docx không.") from e
        sections=infer_import_sections(text, filename)
        return {"kind": "text", "text": sections["english"], "meta": {"title": sections["title"], "question": sections["question"]}}
    content = data.get("text", "")
    if not isinstance(content, str) or len(content.encode("utf-8")) > 2_500_000:
        raise ValueError("Nội dung tệp quá lớn hoặc không hợp lệ.")
    if suffix in ("json",):
        try: parsed = json.loads(content)
        except json.JSONDecodeError as e: raise ValueError("Tệp JSON không hợp lệ.") from e
        if isinstance(parsed, dict) and isinstance(parsed.get("lessons"), list): items=parsed["lessons"]
        elif isinstance(parsed, list): items=parsed
        elif isinstance(parsed, dict) and ("title" in parsed or "question" in parsed): items=[parsed]
        else: return {"kind":"text", "text":json.dumps(parsed, ensure_ascii=False, indent=2), "meta":{"title":filename.rsplit(".",1)[0]}}
        return {"kind":"bulk-json", "lessons":items[:100]}
    if suffix == "csv":
        try: items=list(csv.DictReader(io.StringIO(content)))
        except Exception as e: raise ValueError("Không đọc được CSV.") from e
        for item in items:
            vi=item.get("vietnamese", "")
            if isinstance(vi,str): item["vietnamese"]=[line.strip() for line in vi.replace("|||", "\n").splitlines() if line.strip()]
            en=item.get("english", "")
            if isinstance(en,str) and "essay" not in item: item["english"]=en
        return {"kind":"bulk-json", "lessons":items[:100]}
    if suffix in ("html", "htm"):
        parser=TextOnlyParser(); parser.feed(content)
        # The HTML extractor emits line breaks for block elements; do not flatten them,
        # because paragraph boundaries must survive import into the reverse-translation editor.
        content=re.sub(r"[ \t]+", " ", "".join(parser.parts))
        content=re.sub(r"\n{3,}", "\n\n", content).strip()
    sections=infer_import_sections(content.strip(), filename)
    return {"kind":"text", "text":sections["english"], "meta":{"title":sections["title"], "question":sections["question"]}}


def load_scoring_resources():
    rubric_path = BASE / "resources" / "band_rubric.json"
    refs_path = BASE / "resources" / "reference_catalog.json"
    try:
        rubric_data = json.loads(rubric_path.read_text(encoding="utf-8"))
    except Exception:
        rubric_data = {"bands": {}}
    try:
        references = json.loads(refs_path.read_text(encoding="utf-8"))
    except Exception:
        references = []
    try:
        with db() as c:
            rows = c.execute("SELECT id,payload_json,deleted FROM reference_overrides").fetchall()
        hidden=set(); added=[]
        for row in rows:
            if row["deleted"]: hidden.add(row["id"]); continue
            try:
                item=json.loads(row["payload_json"] or "{}")
                if item: added.append(item)
            except Exception: pass
        references=[r for r in references if str(r.get("id", r.get("url", r.get("title", "")))) not in hidden]
        references.extend(added)
    except Exception:
        pass
    return rubric_data, references


def build_scoring_rubric(task):
    data, _ = load_scoring_resources()
    criterion_codes = ["TA", "CC", "LR", "GRA"] if task == "Task 1" else ["TR", "CC", "LR", "GRA"]
    labels = data.get("criterion_names", {})
    lines = [
        "Bạn là giám khảo luyện IELTS Writing. Đây là đánh giá học tập độc lập, không phải điểm IELTS chính thức.",
        "NGUỒN: IELTS Writing Band Descriptors, cập nhật May 2023: https://ielts.org/cdn/ielts-guides/ielts-writing-band-descriptors.pdf",
        "Các mô tả sau là diễn giải tiếng Việt do WriteWiser biên soạn, không phải bản chép nguyên văn. Khi cần xác minh, người dùng phải mở tài liệu nguồn chính thức.",
        "Đánh giá theo đúng task. Không dùng Task Response cho Task 1; không dùng Task Achievement cho Task 2.",
        "Band anchors theo từng tiêu chí (0–9):"
    ]
    bands = data.get("bands", {})
    for band in ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"]:
        lines.append(f"Band {band}:")
        for code in criterion_codes:
            desc = bands.get(band, {}).get(code, "")
            if desc:
                lines.append(f"- {labels.get(code, code)}: {desc}")
    lines.extend([
        "Phân biệt band liền kề bằng toàn bộ bằng chứng, không chấm theo từ vựng 'cao cấp' đơn thuần. Band 8/9 đòi hỏi kiểm soát ổn định, phù hợp task, không chỉ có vài câu hay.",
        "Không coi bài benchmark hoặc sample là đáp án để bắt chước; đánh giá bài nộp dựa trên yêu cầu đề và rubric.",
        "Task 1 Academic: xem overview, lựa chọn/nhóm đặc điểm, so sánh/số liệu chính xác, không bịa dữ liệu. Task 2: xem mọi phần đề, lập trường, relevance, độ phát triển và hỗ trợ ý.",
        "CC: xem logic, progression, paragraphing, cohesion/reference; LR: range, precision, collocation, register, word formation/spelling; GRA: range, accuracy, sentence control, punctuation.",
        "Nếu bài dưới 150 từ (Task 1) hoặc 250 từ (Task 2), nêu thiếu bằng chứng có thể ảnh hưởng đánh giá, nhưng không tự động trừ band theo một công thức cứng.",
        "Mỗi band tiêu chí là số nguyên từ 0–9, không dùng .5. Cung cấp dẫn chứng ngắn nguyên văn đúng với bài; không bịa quote. Phân biệt lỗi khách quan với lựa chọn phong cách.",
        "Nếu chưa đủ bằng chứng cho một band, nói rõ điều chưa chắc chắn thay vì khẳng định quá mức.",
        "Trả JSON hợp lệ theo schema:",
        '{"overall_band":7.0,"word_count":250,"task_criterion":"TR","criteria":[{"name":"Task Response","band":7,"rationale":"...","strengths":["..."],"weaknesses":["..."],"evidence":["exact quote"],"improvements":["specific rewrite and why"],"next_band_gap":"..."}],"overall_summary":"...","strengths":["..."],"priority_errors":[{"original":"exact quote","correction":"...","explanation":"..."}],"paragraph_feedback":[{"paragraph":"Introduction","comment":"...","suggestions":["..."]}],"upgraded_version":"...","next_steps":["..."],"caveat":"Ước lượng học tập, không phải điểm IELTS chính thức."}',
        "Trả đủ bốn tiêu chí; mảng không có dữ liệu là []. Viết phản hồi bằng tiếng Việt, có gạch đầu dòng trong các danh sách.",
        "MỨC ĐỘ CHI TIẾT BẮT BUỘC: với MỖI tiêu chí hãy nêu Điểm tốt, Điểm cần cải thiện, dẫn chứng nguyên văn cụ thể, giải thích vì sao bằng chứng đáp ứng/chưa đáp ứng descriptor ở band được chọn và vì sao chưa lên band tiếp theo. Đưa ra câu sửa cụ thể và giải thích sự thay đổi về ngữ pháp, từ vựng, collocation, logic hoặc độ chính xác. Không chỉ dùng lời khen chung chung.",
        "Với Task 1, kiểm tra từng nhận định so với ảnh nếu ảnh được đính kèm; xác định đơn vị, nhóm dữ liệu, tỷ trọng so với tổng lượng tuyệt đối, overview có bao quát tất cả biểu đồ, lựa chọn key features, so sánh và độ chính xác số liệu. Không bịa dữ liệu. Nếu không có ảnh, nói rõ không thể kiểm tra trực quan.",
        "Với Task 2, đánh giá từng phần của câu hỏi, độ rõ của lập trường, độ liên quan của từng luận điểm, chuỗi giải thích/cơ chế/ví dụ, và mức phát triển thay vì chỉ đếm số ý. Nhận xét theo từng đoạn và chỉ ra vị trí câu cần sửa.",
        "Trong criteria, nếu phù hợp hãy điền strengths[], weaknesses[], evidence[], improvements[] và next_band_gap. Với priority_errors, ưu tiên lỗi có ảnh hưởng đến nghĩa, độ chính xác hoặc band; mỗi mục phải có trích dẫn nguyên văn, bản sửa và lý do. upgraded_version phải giữ nguyên ý và dữ liệu người viết, không tự thêm số liệu/ví dụ như thể là sự thật."
    ])
    master_path = BASE / "resources" / "ielts_writing_master_prompt.txt"
    try:
        master_prompt = master_path.read_text(encoding="utf-8")
    except Exception:
        master_prompt = ""
    if master_prompt.strip():
        lines.extend(["", "=== MASTER PROMPT DO NGƯỜI DÙNG CUNG CẤP — ÁP DỤNG TRỰC TIẾP CHO LƯỢT CHẤM NÀY ===", master_prompt])
    try:
        benchmark_path = BASE / "resources" / "official_benchmark_catalog.json"
        benchmark_catalog = json.loads(benchmark_path.read_text(encoding="utf-8"))
    except Exception:
        benchmark_catalog = []
    lines.extend(["", "=== THAM CHIẾU SAMPLE ĐƯỢC ĐỊNH DANH TRONG CATALOG ===", json.dumps(benchmark_catalog, ensure_ascii=False), "Lưu ý: catalog này chỉ mô tả các sample đã có nguồn/band được xác nhận; không được giả vờ rằng nó chứa sample được chấm chính thức cho mọi band 5–9. Khi thiếu sample ở band cụ thể, hãy coi các anchor trong descriptor là căn cứ chính và chỉ tạo sample hiệu chuẩn mô phỏng nếu cần; phải ghi rõ sample mô phỏng không phải bài chính thức."])
    lines.extend(["", "QUY TẮC ĐẦU RA BẮT BUỘC CỦA WRITEWISER v13: điểm của từng tiêu chí phải là SỐ NGUYÊN từ 0 đến 9, tuyệt đối không dùng .5. Overall được tính trung bình bốn tiêu chí và làm tròn tới band 0.5 gần nhất."])
    # Include only a small number of admin-approved calibration examples; never user-private edits.
    try:
        with db() as c:
            rows=c.execute("SELECT payload_json FROM admin_calibrations WHERE active=1 ORDER BY created_at DESC LIMIT 3").fetchall()
        examples=[]
        for row in reversed(rows):
            try:
                item=json.loads(row["payload_json"])
                examples.append({k:item.get(k) for k in ("task","question","essay_excerpt","corrected_bands","reasoning") if item.get(k) not in (None,"",[])})
            except Exception: pass
        if examples:
            lines.extend(["", "=== HIỆU CHUẨN ĐƯỢC ADMIN DUYỆT (chỉ là ví dụ bổ trợ; descriptor vẫn là căn cứ chính) ===", json.dumps(examples,ensure_ascii=False)])
    except Exception:
        pass
    return "\n".join(lines)

def gemini_generate(system_instruction, prompt, max_output_tokens=6500, image_data=None):
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Chưa cấu hình GEMINI_API_KEY. Hãy tạo key trong Google AI Studio và thiết lập biến môi trường.")
    model = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{3,100}", model): model="gemini-3.8-flash"
    payload = {
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "contents": [{"role": "user", "parts": ([{"text": prompt}] + ([{"inlineData": {"mimeType": image_data.split(';')[0].split(':')[1], "data": image_data.split(',',1)[1]}}] if image_data and image_data.startswith("data:image/") and "," in image_data else []))}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": max_output_tokens, "responseMimeType": "application/json"}
    }
    request = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            result=json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail=json.loads(e.read().decode("utf-8")); message=detail.get("error",{}).get("message","")
        except Exception: message=""
        if e.code in (401,403): raise RuntimeError("Gemini API key không hợp lệ hoặc chưa có quyền truy cập. " + message[:200])
        if e.code == 429: raise RuntimeError("Gemini đang giới hạn tốc độ hoặc đã hết quota miễn phí. Hãy kiểm tra hạn mức trong AI Studio.")
        raise RuntimeError(f"Gemini API gặp lỗi HTTP {e.code}. {message[:200]}")
    except (urllib.error.URLError, TimeoutError) as e:
        raise RuntimeError("Không kết nối được Gemini API. Hãy kiểm tra kết nối mạng.") from e
    candidates=result.get("candidates", [])
    text="\n".join(part.get("text","") for part in (candidates[0].get("content",{}).get("parts",[]) if candidates else []) if isinstance(part,dict)).strip()
    if not text: raise RuntimeError("Gemini không trả về nội dung. Hãy thử lại hoặc kiểm tra quota.")
    text=re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    try: parsed=json.loads(text)
    except json.JSONDecodeError as e: raise RuntimeError("Gemini trả về JSON không hợp lệ. Hãy thử lại.") from e
    return parsed, result.get("modelVersion", model)


def custom_openai_compatible_providers():
    """Load additional OpenAI-compatible providers entirely from environment variables.

    CUSTOM_AI_PROVIDERS_JSON is a JSON array. API keys stay in separate secret env vars.
    Non-OpenAI-compatible APIs need a dedicated adapter in code.
    """
    raw = os.environ.get("CUSTOM_AI_PROVIDERS_JSON", "").strip()
    if not raw:
        return {}
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(entries, list):
        return {}
    result = {}
    reserved = {"gemini", "groq", "together", "openrouter"}
    for item in entries:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip().lower()
        api_key_env = str(item.get("api_key_env", "")).strip()
        model_env = str(item.get("model_env", "")).strip()
        endpoint = str(item.get("endpoint", "")).strip()
        default_model = str(item.get("default_model", "")).strip()
        label = str(item.get("label", name)).strip() or name
        if (not re.fullmatch(r"[a-z][a-z0-9_-]{1,31}", name) or name in reserved
                or name in result or not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", api_key_env)
                or not model_env or not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", model_env)
                or not default_model):
            continue
        parsed = urlparse(endpoint)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or not endpoint.endswith("/chat/completions"):
            continue
        result[name] = {
            "key_env": api_key_env, "model_env": model_env, "endpoint": endpoint,
            "default_model": default_model, "label": label, "custom": True,
        }
    return result


def parse_json_response_content(content):
    """Parse strict JSON, fenced JSON, or a JSON object embedded in harmless model prose."""
    text = str(content or "").strip().lstrip("\ufeff")
    if not text:
        raise ValueError("empty")
    # Prefer a fenced JSON block if a model ignored JSON-only instructions.
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.I | re.S)
    candidates = [fenced.group(1).strip()] if fenced else []
    candidates.append(text)
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            pass
    # Find the first balanced object/array without being confused by braces in strings.
    for begin, close in (("{", "}"), ("[", "]")):
        for i, char in enumerate(text):
            if char != begin:
                continue
            stack = []
            in_string = False
            escaped = False
            pairs = {"{": "}", "[": "]"}
            for j in range(i, len(text)):
                c = text[j]
                if in_string:
                    if escaped:
                        escaped = False
                    elif c == "\\":
                        escaped = True
                    elif c == '"':
                        in_string = False
                    continue
                if c == '"':
                    in_string = True
                elif c in pairs:
                    stack.append(pairs[c])
                elif c in ("}", "]"):
                    if not stack or c != stack.pop():
                        break
                    if not stack:
                        candidate = text[i:j+1]
                        try:
                            return json.loads(candidate)
                        except json.JSONDecodeError:
                            break
    raise ValueError("invalid")


def openai_compatible_generate(provider, system_instruction, prompt, max_output_tokens=6500, image_data=None):
    builtins = {
        "groq": {"key_env":"GROQ_API_KEY", "model_env":"GROQ_MODEL", "endpoint":"https://api.groq.com/openai/v1/chat/completions", "default_model":"llama-3.3-70b-versatile", "label":"Groq"},
        "together": {"key_env":"TOGETHER_API_KEY", "model_env":"TOGETHER_MODEL", "endpoint":"https://api.together.xyz/v1/chat/completions", "default_model":"meta-llama/Llama-3.3-70B-Instruct-Turbo", "label":"Together AI"},
        "openrouter": {"key_env":"OPENROUTER_API_KEY", "model_env":"OPENROUTER_MODEL", "endpoint":"https://openrouter.ai/api/v1/chat/completions", "default_model":"openai/gpt-4o-mini", "label":"OpenRouter"},
    }
    config = builtins.get(provider) or custom_openai_compatible_providers().get(provider)
    if not config:
        raise RuntimeError(f"Provider '{provider}' chưa được cấu hình. Kiểm tra CUSTOM_AI_PROVIDERS_JSON và AI_PROVIDER_ORDER.")
    key=os.environ.get(config["key_env"], "").strip()
    if not key: raise RuntimeError(f"Chưa cấu hình {config['key_env']}.")
    model=os.environ.get(config["model_env"], config["default_model"]).strip() or config["default_model"]
    user_content = prompt
    if image_data and image_data.startswith("data:image/") and "," in image_data:
        user_content=[{"type":"text","text":prompt},{"type":"image_url","image_url":{"url":image_data}}]
    payload={"model":model,"messages":[{"role":"system","content":system_instruction},{"role":"user","content":user_content}],"temperature":0.2,"max_tokens":max_output_tokens,"response_format":{"type":"json_object"}}
    endpoint=config["endpoint"]
    headers={
        "Authorization":"Bearer "+key,
        "Content-Type":"application/json",
        "Accept":"application/json",
        # Use an explicit, transparent application UA rather than urllib's generic default.
        "User-Agent":os.environ.get("WRITEWISER_USER_AGENT", "WriteWiser/31 (IELTS Writing learning app; contact https://www.facebook.com/people/WriteWiser/61595026757886/)").strip(),
    }
    is_openrouter = "openrouter.ai" in (urlparse(endpoint).hostname or "").lower()
    if is_openrouter:
        headers["HTTP-Referer"]=os.environ.get("OPENROUTER_SITE_URL", os.environ.get("PUBLIC_BASE_URL", "https://writewiser.onrender.com")).strip()
        headers["X-Title"]=os.environ.get("OPENROUTER_APP_NAME","WriteWiser")
        # The healing plugin can repair malformed JSON responses. Require a route that honors
        # response_format; otherwise a retry below can use a less strict compatibility request.
        if os.environ.get("OPENROUTER_RESPONSE_HEALING", "1").strip().lower() not in ("0", "false", "no", "off"):
            payload["plugins"]=[{"id":"response-healing"}]
        payload["provider"]={"require_parameters":True}

    def send(request_payload):
        req=urllib.request.Request(endpoint,data=json.dumps(request_payload,ensure_ascii=False).encode("utf-8"),headers=headers,method="POST")
        with urllib.request.urlopen(req,timeout=120) as resp:
            raw_body = resp.read().decode("utf-8", errors="replace")
        try:
            return json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{provider} trả envelope API không phải JSON hợp lệ (HTTP 200).") from exc

    try:
        result=send(payload)
    except urllib.error.HTTPError as e:
        detail=e.read().decode("utf-8",errors="ignore")[:350]
        # OpenRouter can have an upstream endpoint without support for one of the optional
        # JSON parameters/plugins. Try one compatibility fallback only for parameter errors.
        if is_openrouter and e.code in (400, 404, 422) and re.search(r"response_format|require_parameters|response.healing|plugin|unsupported parameter|no endpoints", detail, re.I):
            fallback=dict(payload)
            fallback.pop("response_format", None)
            fallback.pop("plugins", None)
            fallback.pop("provider", None)
            try:
                result=send(fallback)
                payload=fallback  # reuse the working compatibility shape for any JSON repair retry
            except urllib.error.HTTPError as retry_error:
                retry_detail=retry_error.read().decode("utf-8",errors="ignore")[:280]
                raise RuntimeError(f"{provider} HTTP {retry_error.code} sau khi thử cấu hình tương thích: {retry_detail}") from retry_error
        else:
            raise RuntimeError(f"{provider} HTTP {e.code}: {detail}") from e
    except (urllib.error.URLError,TimeoutError) as e:
        raise RuntimeError(f"{provider}: lỗi kết nối hoặc timeout.") from e

    def extract_response(response):
        choices=response.get("choices") or []
        if not choices:
            raise ValueError("no_choices")
        choice=choices[0] or {}
        message=choice.get("message") or {}
        content=message.get("content")
        if isinstance(content,list):
            content="".join(str(x.get("text", "")) for x in content if isinstance(x,dict) and x.get("type", "text") == "text")
        if not isinstance(content,str) or not content.strip():
            if message.get("refusal"):
                raise ValueError("refusal")
            raise ValueError("empty")
        try:
            parsed=parse_json_response_content(content)
        except ValueError:
            finish=choice.get("finish_reason")
            if finish in ("length", "max_tokens"):
                raise ValueError("truncated")
            raise ValueError("invalid")
        if not isinstance(parsed, (dict, list)):
            raise ValueError("wrong_root")
        return parsed, choice.get("finish_reason"), content

    try:
        parsed, finish_reason, content = extract_response(result)
        return parsed, f"{provider}:{result.get('model', model)}"
    except ValueError as first_error:
        # OpenRouter outputs from some routes occasionally add prose or malformed/truncated JSON.
        # Repair once on the same provider only when parsing failed; normal calls use one request.
        if is_openrouter and os.environ.get("OPENROUTER_JSON_RETRY", "1").strip().lower() not in ("0", "false", "no", "off"):
            repair_system = system_instruction + "\n\nCRITICAL OUTPUT RULE: Your last response was rejected because it was not a complete valid JSON object/array. Return only one complete JSON value. No Markdown fences, no preamble, no explanation outside JSON. Preserve the required schema and all required fields."
            preview = str((result.get("choices") or [{}])[0].get("message", {}).get("content", ""))[:4500]
            repair_prompt = prompt + "\n\nYour previous response was not parseable JSON. Re-generate the answer from the original instructions as one complete valid JSON object, without Markdown or surrounding prose. The previous invalid output is provided only to diagnose formatting; do not copy its malformed syntax:\n" + preview
            retry_payload=dict(payload)
            retry_payload["messages"]=[{"role":"system","content":repair_system},{"role":"user","content":repair_prompt}]
            retry_payload["max_tokens"]=min(max(max_output_tokens+1600, 3000), 10000)
            try:
                retry_result=send(retry_payload)
                parsed, _, _=extract_response(retry_result)
                return parsed, f"{provider}:{retry_result.get('model', model)} (JSON retry)"
            except urllib.error.HTTPError as retry_error:
                retry_detail=retry_error.read().decode("utf-8",errors="ignore")[:240]
                raise RuntimeError(f"{provider} không sửa được JSON ở lượt thử lại (HTTP {retry_error.code}): {retry_detail}") from retry_error
            except (urllib.error.URLError,TimeoutError) as retry_error:
                raise RuntimeError(f"{provider} không kết nối được ở lượt sửa JSON.") from retry_error
            except ValueError as retry_error:
                first_error=retry_error
        reasons={"empty":"nội dung trống", "no_choices":"không có choices", "refusal":"model từ chối yêu cầu", "truncated":"JSON bị cắt vì chạm giới hạn token", "wrong_root":"JSON có kiểu gốc không phải object/array", "invalid":"nội dung không phải JSON hoàn chỉnh"}
        raise RuntimeError(f"{provider} trả phản hồi không dùng được: {reasons.get(str(first_error), 'không phân tích được JSON')}. Đã bật thử sửa JSON cho OpenRouter nếu có thể.") from first_error


def ai_generate(system_instruction, prompt, max_output_tokens=6500, image_data=None):
    order=[x.strip().lower() for x in os.environ.get("AI_PROVIDER_ORDER","gemini,groq,together,openrouter").split(",") if x.strip()]
    custom = custom_openai_compatible_providers()
    available={
        "gemini":bool(os.environ.get("GEMINI_API_KEY")),
        "groq":bool(os.environ.get("GROQ_API_KEY")),
        "together":bool(os.environ.get("TOGETHER_API_KEY")),
        "openrouter":bool(os.environ.get("OPENROUTER_API_KEY")),
    }
    for name, cfg in custom.items():
        available[name]=bool(os.environ.get(cfg["key_env"], "").strip())
    failures=[]
    for provider in order:
        if provider not in available or not available[provider]: continue
        try:
            if provider=="gemini": return gemini_generate(system_instruction,prompt,max_output_tokens,image_data)
            return openai_compatible_generate(provider,system_instruction,prompt,max_output_tokens,image_data)
        except RuntimeError as exc:
            failures.append(f"{provider}: {str(exc)[:240]}")
            # Any provider error triggers the next configured provider: 503, 429, invalid JSON, etc.
    if not failures:
        custom_note = " Hãy kiểm tra CUSTOM_AI_PROVIDERS_JSON nếu muốn thêm endpoint tương thích OpenAI." 
        raise RuntimeError("Chưa cấu hình AI provider đang hoạt động. Hãy đặt ít nhất một API key (GEMINI_API_KEY, GROQ_API_KEY, TOGETHER_API_KEY, OPENROUTER_API_KEY hoặc API key được khai báo trong CUSTOM_AI_PROVIDERS_JSON)." + custom_note)
    raise RuntimeError("Tất cả AI provider khả dụng đều thất bại; đã thử dự phòng theo thứ tự cấu hình. " + " | ".join(failures))

def _number_band(value):
    """Return a finite IELTS band in [0, 9], accepting values like 'Band 7.5'."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            number = float(value)
        else:
            match = re.search(r"(?<!\d)([0-9](?:\.5)?)(?!\d)", str(value))
            if not match: return None
            number = float(match.group(1))
        return number if 0 <= number <= 9 else None
    except (TypeError, ValueError):
        return None

def _criterion_key(name):
    normalized = re.sub(r"[^a-z]", "", str(name or "").lower())
    if normalized in ("ta", "taskachievement", "taskachievementacademic", "achievement"): return "TA/TR"
    if normalized in ("tr", "taskresponse", "responsetotask"): return "TA/TR"
    if normalized in ("cc", "coherenceandcohesion", "coherencecohesion"): return "CC"
    if normalized in ("lr", "lexicalresource", "lexical"): return "LR"
    if normalized in ("gra", "grammaticalrangeandaccuracy", "grammar", "grammaticalrangeaccuracy"): return "GRA"
    return None

def _summarize_writing_history(history):
    """Only scored free-writing submissions contribute to IELTS band analytics."""
    by_task = {"Task 1": [], "Task 2": []}
    for record in history if isinstance(history, list) else []:
        if not isinstance(record, dict) or record.get("mode") != "independent":
            continue
        task = record.get("task")
        feedback = record.get("feedback")
        if task not in by_task or not isinstance(feedback, dict):
            continue
        overall = _number_band(feedback.get("overall_band"))
        if overall is None:
            continue
        criteria = {"TA/TR": None, "CC": None, "LR": None, "GRA": None}
        raw_criteria = feedback.get("criteria", [])
        if isinstance(raw_criteria, list):
            for item in raw_criteria:
                if not isinstance(item, dict): continue
                key = _criterion_key(item.get("name") or item.get("criterion") or item.get("key"))
                value = _number_band(item.get("band", item.get("score")))
                if key and value is not None:
                    criteria[key] = value
        by_task[task].append({"overall": overall, "criteria": criteria, "date": record.get("date"), "id": record.get("id")})
    def avg(vals):
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals), 2) if vals else None
    summary = {}
    for task, records in by_task.items():
        summary[task] = {
            "count": len(records),
            "averageOverall": avg([r["overall"] for r in records]),
            "criteria": {key: avg([r["criteria"].get(key) for r in records]) for key in ("TA/TR", "CC", "LR", "GRA")},
            "distribution": {str(band / 2): sum(1 for r in records if round(r["overall"] * 2) == band) for band in range(0, 19)}
        }
    return summary

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE), **kwargs)

    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

    def send_json(self, status, obj, extra_headers=None):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; base-uri 'self'; frame-ancestors 'none'")
        if extra_headers:
            for k, v in extra_headers:
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length < 0 or length > MAX_BODY:
            raise ValueError("Nội dung gửi lên quá lớn.")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("Dữ liệu JSON không hợp lệ.")
        if not isinstance(value, dict):
            raise ValueError("Dữ liệu gửi lên phải là một object.")
        return value

    def session_user(self):
        cookie_header = self.headers.get("Cookie", "")
        jar = cookies.SimpleCookie()
        try:
            jar.load(cookie_header)
            token = jar[COOKIE_NAME].value if COOKIE_NAME in jar else None
        except cookies.CookieError:
            token = None
        if not token:
            return None
        now = int(time.time())
        with db() as c:
            row = c.execute("""SELECT users.id, users.username, users.role
                               FROM sessions JOIN users ON users.id=sessions.user_id
                               WHERE sessions.token_hash=? AND sessions.expires_at>?""",
                            (token_hash(token), now)).fetchone()
        return dict(row) if row else None

    def set_session(self, token, max_age=SESSION_TTL):
        value = f"{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}"
        if os.environ.get("DRAFTWISE_SECURE_COOKIE") == "1":
            value += "; Secure"
        return [("Set-Cookie", value)]

    def clear_session(self):
        value = f"{COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"
        if os.environ.get("DRAFTWISE_SECURE_COOKIE") == "1":
            value += "; Secure"
        return [("Set-Cookie", value)]

    def gate_user(self):
        jar = cookies.SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
            token = jar["writewiser_gate"].value if "writewiser_gate" in jar else None
        except cookies.CookieError:
            token = None
        if not token:
            return False
        now = int(time.time())
        with db() as c:
            row = c.execute("SELECT token_hash FROM gate_sessions WHERE token_hash=? AND expires_at>?", (token_hash(token), now)).fetchone()
        return bool(row)

    def set_gate_cookie(self, token):
        ttl = 60 * 60 * 12
        value = f"writewiser_gate={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={ttl}"
        if os.environ.get("DRAFTWISE_SECURE_COOKIE") == "1": value += "; Secure"
        return [("Set-Cookie", value)]

    def clear_gate_cookie(self):
        value = "writewiser_gate=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"
        if os.environ.get("DRAFTWISE_SECURE_COOKIE") == "1": value += "; Secure"
        return [("Set-Cookie", value)]

    def require_admin(self):
        user = self.session_user()
        if not user or user.get("role") != "admin":
            self.send_json(403, {"error": "Chỉ tài khoản admin mới được thực hiện thao tác này."})
            return None
        return user

    def clean_lesson_payload(self, value, admin_username):
        if not isinstance(value, dict): raise ValueError("Thiếu dữ liệu đề.")
        lesson = dict(value)
        if lesson.get("task") not in ("Task 1", "Task 2"): raise ValueError("Task phải là Task 1 hoặc Task 2.")
        for key, maxlen in (("title", 180), ("type", 100), ("topic", 100), ("question", 5000)):
            item = lesson.get(key)
            if not isinstance(item, str) or not item.strip() or len(item) > maxlen: raise ValueError(f"Trường {key} không hợp lệ.")
            lesson[key] = item.strip()
        essay = lesson.get("essay")
        if not isinstance(essay, list) or not essay or len(essay) > 60: raise ValueError("Bài mẫu phải có từ 1 đến 60 câu.")
        clean = []
        for item in essay:
            if not isinstance(item, dict) or not isinstance(item.get("en"), str) or not isinstance(item.get("vi"), str) or not item["en"].strip() or not item["vi"].strip():
                raise ValueError("Mỗi câu phải có tiếng Anh và bản dịch tiếng Việt.")
            paragraph_index=item.get("paragraphIndex",0)
            if not isinstance(paragraph_index,int) or paragraph_index<0 or paragraph_index>59: paragraph_index=0
            clean.append({"en": item["en"].strip()[:2500], "vi": item["vi"].strip()[:2500], "hint": str(item.get("hint", ""))[:1000], "paragraphIndex": paragraph_index})
        lesson["essay"] = clean
        lesson["id"] = "global-" + secrets.token_urlsafe(10).replace("-", "").replace("_", "")
        lesson["time"] = 20 if lesson["task"] == "Task 1" else 40
        lesson["custom"] = True
        lesson["publishedGlobal"] = True
        lesson["createdBy"] = admin_username
        lesson["createdAt"] = lesson.get("createdAt") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return lesson

    def do_GET(self):
        path = urlparse(self.path).path
        public_gate_paths = {"/api/health", "/api/gate/status"}
        if path.startswith("/api/") and path not in public_gate_paths and not self.gate_user():
            return self.send_json(423, {"error": "Nhập mật khẩu truy cập chung để mở WriteWiser."})
        if path.startswith("/api/"):
            if path == "/api/gate/status":
                return self.send_json(200, {"unlocked": self.gate_user()})
            if path == "/api/library-overrides":
                with db() as c:
                    rows = c.execute("SELECT id,lesson_json,deleted FROM library_overrides").fetchall()
                items=[]
                for row in rows:
                    try: items.append({"id":row["id"],"lesson":json.loads(row["lesson_json"]) if row["lesson_json"] else None,"deleted":bool(row["deleted"])})
                    except Exception: pass
                return self.send_json(200,{"items":items})
            if path == "/api/global-lessons":
                with db() as c:
                    rows = c.execute("SELECT lesson_json FROM global_lessons ORDER BY created_at DESC").fetchall()
                lessons=[]
                for row in rows:
                    try: lessons.append(json.loads(row["lesson_json"]))
                    except Exception: pass
                return self.send_json(200, {"lessons": lessons})
            if path == "/api/health":
                return self.send_json(200, {"ok": True, "service": "writewiser", "version": "32-dashboard-history-scroll", "database": "postgres" if DATABASE_URL else "sqlite-local", "aiConfigured": any(os.environ.get(k) for k in ("GEMINI_API_KEY","GROQ_API_KEY","TOGETHER_API_KEY","OPENROUTER_API_KEY")) or any(os.environ.get(c.get("key_env", "")) for c in custom_openai_compatible_providers().values()), "providers": [p for p,k in (("Gemini","GEMINI_API_KEY"),("Groq","GROQ_API_KEY"),("Together AI","TOGETHER_API_KEY"),("OpenRouter","OPENROUTER_API_KEY")) if os.environ.get(k)] + [cfg["label"] for name,cfg in custom_openai_compatible_providers().items() if os.environ.get(cfg["key_env"])], "providerOrder": os.environ.get("AI_PROVIDER_ORDER","gemini,groq,together,openrouter"), "model": os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")})
            if path == "/api/scoring-status":
                prompt_path = BASE / "resources" / "ielts_writing_master_prompt.txt"
                try:
                    master_prompt = prompt_path.read_text(encoding="utf-8")
                    return self.send_json(200, {"masterPromptLoaded": bool(master_prompt.strip()), "promptChars": len(master_prompt), "promptSha256": hashlib.sha256(master_prompt.encode("utf-8")).hexdigest(), "promptFile": "resources/ielts_writing_master_prompt.txt", "appliedTo": "/api/writing-feedback", "criteriaBands": "integer 0-9", "overallBand": "nearest half-band", "version": "13"})
                except Exception as exc:
                    return self.send_json(500, {"masterPromptLoaded": False, "error": "Không đọc được prompt chấm bài: " + str(exc)[:180]})
            if path == "/api/admin/calibrations":
                admin=self.require_admin()
                if not admin:return
                with db() as c: rows=c.execute("SELECT id,payload_json,created_by,created_at,active FROM admin_calibrations ORDER BY created_at DESC").fetchall()
                return self.send_json(200,{"items":[{"id":r["id"],"payload":json.loads(r["payload_json"]),"createdBy":r["created_by"],"createdAt":r["created_at"],"active":bool(r["active"])} for r in rows]})
            if path == "/api/admin/feedback-corrections":
                admin=self.require_admin()
                if not admin:return
                with db() as c: rows=c.execute("SELECT fc.id,fc.payload_json,fc.status,fc.created_at,u.username FROM feedback_corrections fc JOIN users u ON u.id=fc.user_id ORDER BY fc.created_at DESC LIMIT 100").fetchall()
                return self.send_json(200,{"items":[{"id":r["id"],"payload":json.loads(r["payload_json"]),"status":r["status"],"createdAt":r["created_at"],"username":r["username"]} for r in rows]})
            if path == "/api/scoring-resources":
                rubric_data, references = load_scoring_resources()
                try:
                    benchmark_catalog = json.loads((BASE / "resources" / "official_benchmark_catalog.json").read_text(encoding="utf-8"))
                except Exception:
                    benchmark_catalog = []
                return self.send_json(200, {"rubricVersion": rubric_data.get("version", "unknown"), "rubricSource": rubric_data.get("source", {}), "bands": rubric_data.get("bands", {}), "criterionNames": rubric_data.get("criterion_names", {}), "references": references, "officialBenchmarks": benchmark_catalog})
            if path == "/api/dol-catalog":
                query = urlparse(self.path).query
                from urllib.parse import parse_qs
                task = parse_qs(query).get("task", ["all"])[0]
                if task not in ("all", "Task 1", "Task 2"):
                    return self.send_json(400, {"error": "Bộ lọc Task không hợp lệ."})
                try:
                    items = fetch_dol_catalog(task)
                    return self.send_json(200, {"items": items, "count": len(items), "source": "DOL public listing pages", "contentImported": False})
                except Exception as e:
                    return self.send_json(502, {"error": "Không tải được danh mục DOL lúc này. Bạn vẫn có thể mở các liên kết DOL có sẵn. Chi tiết: " + str(e)[:180]})
            if path == "/api/me":
                user = self.session_user()
                return self.send_json(200, {"authenticated": bool(user), "user": user})
            if path == "/api/state":
                user = self.session_user()
                if not user:
                    return self.send_json(401, {"error": "Bạn cần đăng nhập."})
                with db() as c:
                    row = c.execute("SELECT state_json, updated_at FROM user_state WHERE user_id=?", (user["id"],)).fetchone()
                if not row:
                    return self.send_json(200, {"state": None, "updatedAt": None})
                try:
                    state = json.loads(row["state_json"])
                except json.JSONDecodeError:
                    state = None
                return self.send_json(200, {"state": state, "updatedAt": row["updated_at"]})
            if path == "/api/dashboard":
                user = self.session_user()
                if not user:
                    return self.send_json(401, {"error": "Đăng nhập để xem Dashboard và bảng xếp hạng."})
                with db() as c:
                    mine = c.execute("SELECT state_json FROM user_state WHERE user_id=?", (user["id"],)).fetchone()
                    all_rows = c.execute("SELECT users.username, users.role, user_state.state_json FROM users LEFT JOIN user_state ON user_state.user_id=users.id WHERE users.role='user' ORDER BY users.username").fetchall()
                try:
                    own_state = json.loads(mine["state_json"]) if mine and mine["state_json"] else {}
                except Exception:
                    own_state = {}
                own_history = own_state.get("history", []) if isinstance(own_state, dict) else []
                leaderboard = {"Task 1": [], "Task 2": []}
                for row in all_rows:
                    try:
                        state = json.loads(row["state_json"]) if row["state_json"] else {}
                    except Exception:
                        state = {}
                    user_history = state.get("history", []) if isinstance(state, dict) else []
                    stats = _summarize_writing_history(user_history)
                    for task in ("Task 1", "Task 2"):
                        task_stats = stats[task]
                        if task_stats["count"]:
                            leaderboard[task].append({"username": row["username"], "averageOverall": task_stats["averageOverall"], "submissions": task_stats["count"]})
                for task in ("Task 1", "Task 2"):
                    leaderboard[task].sort(key=lambda item: (-(item["averageOverall"] or 0), -item["submissions"], item["username"].lower()))
                    for rank, item in enumerate(leaderboard[task], 1): item["rank"] = rank
                return self.send_json(200, {"myHistory": own_history if isinstance(own_history, list) else [], "leaderboard": leaderboard, "updatedAt": int(time.time()), "privacy": "Leaderboard exposes usernames, average overall band by task and number of scored submissions only; no essay text or feedback is shared."})
            return self.send_json(404, {"error": "Không tìm thấy API."})
        # The main document must load so its password overlay can be shown. Other
        # HTML pages (including calibration.html) cannot be opened directly before
        # the server-side gate session is valid.
        if path.endswith(".html") and path not in ("/index.html",) and not self.gate_user():
            self.send_response(302)
            self.send_header("Location", "/")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        # Facebook/Open Graph crawlers need absolute URLs in the raw HTML.
        # Render's host may differ between deployments, so derive the public origin
        # from PUBLIC_BASE_URL (preferred) or the reverse-proxy request headers.
        if path in ("/", "/index.html", "/calibration.html"):
            page_file = BASE / ("calibration.html" if path == "/calibration.html" else "index.html")
            try:
                page_html = page_file.read_text(encoding="utf-8")
            except OSError:
                return self.send_error(404, "Page not found")
            configured_base = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
            origin = ""
            if configured_base:
                parsed_base = urlparse(configured_base)
                if parsed_base.scheme in ("http", "https") and parsed_base.netloc and not parsed_base.username and not parsed_base.password:
                    origin = f"{parsed_base.scheme}://{parsed_base.netloc}"
            if not origin:
                host = (self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or "localhost:8000").split(",")[0].strip()
                if not re.fullmatch(r"[A-Za-z0-9.\-\[\]:]+", host):
                    host = "localhost:8000"
                proto = (self.headers.get("X-Forwarded-Proto") or ("http" if host.startswith(("localhost", "127.0.0.1")) else "https")).split(",")[0].strip().lower()
                if proto not in ("http", "https"):
                    proto = "https"
                origin = f"{proto}://{host}"
            public_path = "/calibration.html" if path == "/calibration.html" else "/"
            public_url = html.escape(origin + public_path, quote=True)
            image_url = html.escape(origin + "/assets/og-image-v30.png", quote=True)
            page_html = page_html.replace("__WW_PUBLIC_URL__", public_url).replace("__WW_OG_IMAGE_URL__", image_url)
            raw = page_html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(raw)
            return
        if path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def rate_limited(self, path):
        """Best-effort in-memory abuse guard for the single-instance free deployment."""
        now = time.time()
        ip = self.client_address[0] if self.client_address else "unknown"
        # Hosting reverse proxies may forward the original client address.
        forwarded = self.headers.get("X-Forwarded-For", "").strip()
        if forwarded:
            ip = forwarded.split(",")[-1].strip() or ip
        if path == "/api/register":
            window, limit = 3600, 5
            identity = ip
        elif path == "/api/login":
            window, limit = 60, 12
            identity = ip
        elif path in ("/api/translate", "/api/feedback", "/api/writing-feedback", "/api/brainstorm-review"):
            window, limit = 60, 12
            jar = cookies.SimpleCookie()
            try:
                jar.load(self.headers.get("Cookie", ""))
                token = jar[COOKIE_NAME].value if COOKIE_NAME in jar else ""
            except cookies.CookieError:
                token = ""
            identity = token_hash(token) if token else ip
        else:
            return False
        key = (identity, path)
        with _RATE_LOCK:
            recent = [t for t in _RATE_BUCKETS.get(key, []) if now - t < window]
            if len(recent) >= limit:
                _RATE_BUCKETS[key] = recent
                return True
            recent.append(now)
            _RATE_BUCKETS[key] = recent
            if len(_RATE_BUCKETS) > 5000:
                for k in list(_RATE_BUCKETS)[:1000]:
                    if not _RATE_BUCKETS[k] or now - _RATE_BUCKETS[k][-1] > 3600:
                        _RATE_BUCKETS.pop(k, None)
        return False

    def do_POST(self):
        path = urlparse(self.path).path
        if path.startswith("/api/") and path != "/api/gate/unlock" and not self.gate_user():
            return self.send_json(423, {"error": "Nhập mật khẩu truy cập chung để mở WriteWiser."})
        if self.rate_limited(path):
            return self.send_json(429, {"error": "Bạn đã gửi quá nhiều yêu cầu trong thời gian ngắn. Hãy chờ một lúc rồi thử lại."})
        if not path.startswith("/api/"):
            return self.send_json(404, {"error": "Không tìm thấy API."})
        try:
            data = self.read_json()
        except ValueError as e:
            return self.send_json(400, {"error": str(e)})
        if path == "/api/gate/unlock":
            password = data.get("password", "")
            if not verify_stored_password("gate_password", password):
                return self.send_json(401, {"error": "Mật khẩu truy cập chung không đúng."})
            token = secrets.token_urlsafe(32)
            now = int(time.time())
            with db() as c:
                c.execute("INSERT INTO gate_sessions(token_hash,expires_at,created_at) VALUES(?,?,?)", (token_hash(token), now + 60*60*12, now))
            return self.send_json(200, {"unlocked": True}, self.set_gate_cookie(token))
        if path == "/api/account/password":
            user = self.session_user()
            if not user: return self.send_json(401,{"error":"Bạn cần đăng nhập."})
            old_password=data.get("oldPassword",""); new_password=data.get("newPassword","")
            with db() as c: row=c.execute("SELECT id,password_hash,salt FROM users WHERE id=?",(user["id"],)).fetchone()
            if not row or not isinstance(old_password,str) or not hmac.compare_digest(password_hash(old_password,row["salt"]),row["password_hash"]):
                return self.send_json(401,{"error":"Mật khẩu hiện tại không đúng."})
            if not isinstance(new_password,str) or len(new_password)<8 or len(new_password)>200:
                return self.send_json(400,{"error":"Mật khẩu mới phải dài từ 8 đến 200 ký tự."})
            salt=secrets.token_hex(16); ph=password_hash(new_password,salt)
            with db() as c: c.execute("UPDATE users SET password_hash=?,salt=? WHERE id=?",(ph,salt,user["id"]))
            return self.send_json(200,{"ok":True})
        if path == "/api/library-overrides":
            admin=self.require_admin()
            if not admin:return
            lesson_id=str(data.get("id", "")).strip()
            if not lesson_id or len(lesson_id)>180:return self.send_json(400,{"error":"ID đề không hợp lệ."})
            lesson=data.get("lesson")
            deleted=1 if data.get("deleted") else 0
            if lesson is not None:
                try: lesson=self.clean_lesson_payload(lesson,admin["username"])
                except ValueError as exc:return self.send_json(400,{"error":str(exc)})
                lesson["id"]=lesson_id
            serialized=json.dumps(lesson,ensure_ascii=False) if lesson is not None else None
            now=int(time.time())
            with db() as c:c.execute("INSERT INTO library_overrides(id,lesson_json,deleted,updated_at,updated_by) VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET lesson_json=excluded.lesson_json,deleted=excluded.deleted,updated_at=excluded.updated_at,updated_by=excluded.updated_by",(lesson_id,serialized,deleted,now,admin["username"]))
            return self.send_json(200,{"ok":True,"id":lesson_id})
        if path == "/api/admin/gate-password":
            admin = self.require_admin()
            if not admin: return
            password = data.get("password", "")
            if not isinstance(password, str) or len(password) < 12 or len(password) > 200:
                return self.send_json(400, {"error": "Mật khẩu web phải dài từ 12 đến 200 ký tự."})
            salt = secrets.token_hex(16)
            ph = password_hash(password, salt)
            now = int(time.time())
            with db() as c:
                c.execute("UPDATE app_settings SET setting_value=?,updated_at=? WHERE setting_key='gate_password_salt'", (salt, now))
                c.execute("UPDATE app_settings SET setting_value=?,updated_at=? WHERE setting_key='gate_password_hash'", (ph, now))
                c.execute("DELETE FROM gate_sessions")
                token = secrets.token_urlsafe(32)
                c.execute("INSERT INTO gate_sessions(token_hash,expires_at,created_at) VALUES(?,?,?)", (token_hash(token), now + 60*60*12, now))
            return self.send_json(200, {"ok": True}, self.set_gate_cookie(token))
        if path == "/api/admin/admin-password":
            admin = self.require_admin()
            if not admin: return
            password = data.get("password", "")
            if not isinstance(password, str) or len(password) < 12 or len(password) > 200:
                return self.send_json(400, {"error": "Mật khẩu admin phải dài từ 12 đến 200 ký tự."})
            salt = secrets.token_hex(16); ph = password_hash(password, salt); now = int(time.time())
            new_token = secrets.token_urlsafe(32)
            with db() as c:
                c.execute("UPDATE users SET password_hash=?,salt=? WHERE id=?", (ph, salt, admin["id"]))
                c.execute("DELETE FROM sessions WHERE user_id=?", (admin["id"],))
                c.execute("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)", (token_hash(new_token), admin["id"], now + SESSION_TTL))
            return self.send_json(200, {"ok": True}, self.set_session(new_token))
        if path == "/api/global-lessons":
            admin = self.require_admin()
            if not admin: return
            try: lesson = self.clean_lesson_payload(data.get("lesson"), admin["username"])
            except ValueError as exc: return self.send_json(400, {"error": str(exc)})
            now = int(time.time())
            serialized = json.dumps(lesson, ensure_ascii=False)
            with db() as c:
                c.execute("INSERT INTO global_lessons(id,lesson_json,created_by,created_at,updated_at) VALUES(?,?,?,?,?)", (lesson["id"], serialized, admin["username"], now, now))
            return self.send_json(201, {"lesson": lesson})
        if path == "/api/register":
            username = str(data.get("username", "")).strip()
            password = data.get("password", "")
            if not USERNAME_RE.fullmatch(username):
                return self.send_json(400, {"error": "Tên đăng nhập phải dài 3–24 ký tự, chỉ gồm chữ, số, dấu chấm, gạch dưới hoặc gạch ngang."})
            if not isinstance(password, str) or len(password) < 5 or len(password) > 200:
                return self.send_json(400, {"error": "Mật khẩu phải có ít nhất 5 ký tự và không quá 200 ký tự."})
            salt = secrets.token_hex(16)
            ph = password_hash(password, salt)
            try:
                with db() as c:
                    cur = c.execute("INSERT INTO users(username,password_hash,salt,created_at) VALUES(?,?,?,?) RETURNING id",
                                    (username, ph, salt, int(time.time())))
                    user_id = cur.fetchone()["id"]
            except Exception as exc:
                if not is_integrity_error(exc):
                    raise
                return self.send_json(409, {"error": "Tên đăng nhập đã tồn tại."})
            token = secrets.token_urlsafe(32)
            with db() as c:
                c.execute("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)",
                          (token_hash(token), user_id, int(time.time()) + SESSION_TTL))
            return self.send_json(201, {"authenticated": True, "user": {"id": user_id, "username": username, "role": "user"}},
                                  self.set_session(token))
        if path == "/api/login":
            username = str(data.get("username", "")).strip()
            password = data.get("password", "")
            with db() as c:
                row = c.execute("SELECT id, username, password_hash, salt, role FROM users WHERE LOWER(username)=LOWER(?)", (username,)).fetchone()
            if not row or not isinstance(password, str) or not hmac.compare_digest(password_hash(password, row["salt"]), row["password_hash"]):
                return self.send_json(401, {"error": "Tên đăng nhập hoặc mật khẩu không đúng."})
            token = secrets.token_urlsafe(32)
            with db() as c:
                c.execute("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)",
                          (token_hash(token), row["id"], int(time.time()) + SESSION_TTL))
            return self.send_json(200, {"authenticated": True, "user": {"id": row["id"], "username": row["username"], "role": row["role"]}},
                                  self.set_session(token))
        if path == "/api/parse-import":
            try:
                parsed=parse_import_file(data)
                return self.send_json(200, parsed)
            except ValueError as e:
                return self.send_json(400, {"error": str(e)})
            except Exception as e:
                return self.send_json(400, {"error": "Không thể phân tích tệp: " + str(e)[:160]})
        if path == "/api/segment-paragraphs":
            sentences=data.get("sentences")
            task=str(data.get("task", "Task 2")).strip()
            if task not in ("Task 1", "Task 2"):
                return self.send_json(400,{"error":"Task phải là Task 1 hoặc Task 2."})
            if not isinstance(sentences,list) or not 4<=len(sentences)<=60 or any(not isinstance(x,str) or not x.strip() or len(x)>2500 for x in sentences):
                return self.send_json(400,{"error":"Cần từ 4 đến 60 câu để phân bài vào đúng 4 đoạn theo cấu trúc IELTS."})
            if task == "Task 1":
                roles=["Introduction","Overview","Body 1","Body 2"]
                guidance="""CẤU TRÚC BẮT BUỘC cho IELTS Academic Writing Task 1: đúng 4 đoạn theo thứ tự sau. 1) Introduction: diễn đạt lại yêu cầu biểu đồ/bảng/bản đồ/quy trình, không đưa overview vào đây. 2) Overview: khái quát các xu hướng/đặc điểm nổi bật, không liệt kê số liệu chi tiết. 3) Body 1: nhóm số liệu/đặc điểm chi tiết thứ nhất theo logic phù hợp. 4) Body 2: nhóm số liệu/đặc điểm chi tiết còn lại để so sánh/đối chiếu. Không tạo Conclusion cho Task 1. Dùng toàn bộ câu đầu vào, giữ nguyên thứ tự câu; không viết lại, sửa, lược bỏ, thêm hoặc nhân bản câu. Nếu bài gốc trộn các chức năng, hãy gán ranh giới theo chức năng chủ đạo của từng câu nhưng vẫn giữ thứ tự."""
            else:
                roles=["Introduction","Body 1","Body 2","Conclusion"]
                guidance="""CẤU TRÚC BẮT BUỘC cho IELTS Writing Task 2: đúng 4 đoạn theo thứ tự sau. 1) Introduction: paraphrase chủ đề và nêu lập trường/định hướng trả lời nếu có. 2) Body 1: luận điểm chính thứ nhất cùng giải thích/ví dụ hỗ trợ. 3) Body 2: luận điểm chính thứ hai hoặc góc nhìn còn lại cùng giải thích/ví dụ hỗ trợ. 4) Conclusion: tổng kết lập luận và nhắc lại câu trả lời/lập trường, không mở ý mới. Dùng toàn bộ câu đầu vào, giữ nguyên thứ tự câu; không viết lại, sửa, lược bỏ, thêm hoặc nhân bản câu. Nếu bài gốc trộn các chức năng, hãy gán ranh giới theo chức năng chủ đạo của từng câu nhưng vẫn giữ thứ tự."""
            system=("Bạn là chuyên gia IELTS Writing và biên tập đoạn văn học thuật. " + guidance + " Trả JSON duy nhất với schema {\"paragraphs\":[{\"role\":\""+roles[0]+"\",\"sentences\":[1,2]},{\"role\":\""+roles[1]+"\",\"sentences\":[3]},{\"role\":\""+roles[2]+"\",\"sentences\":[4]},{\"role\":\""+roles[3]+"\",\"sentences\":[5,6]}]}. Phải có đúng 4 đối tượng, dùng chính xác nhãn role theo thứ tự: " + ", ".join(roles) + ". Mỗi đoạn phải có ít nhất một câu. Chỉ số câu là số nguyên 1-based trong danh sách đầu vào. Mỗi số từ 1 đến N phải xuất hiện đúng một lần, theo thứ tự tăng dần toàn bài, không thiếu, không trùng. Không thêm tiêu đề vào nội dung bài; role chỉ là metadata.")
            prompt="Phân đoạn bài theo đúng cấu trúc bắt buộc của "+task+". Đọc chức năng giao tiếp của các câu rồi tạo đúng 4 đoạn theo nhãn yêu cầu. Chỉ trả JSON.\n"+json.dumps({"task":task,"required_roles":roles,"sentences":[{"number":i+1,"text":x} for i,x in enumerate(sentences)]},ensure_ascii=False)
            try:
                result,model=ai_generate(system,prompt,max_output_tokens=2200)
                paragraphs=result.get("paragraphs") if isinstance(result,dict) else None
                if not isinstance(paragraphs,list) or len(paragraphs)!=4:
                    return self.send_json(502,{"error":"AI chưa trả đúng 4 đoạn theo cấu trúc IELTS. Hãy thử lại hoặc chỉnh dòng trống thủ công."})
                groups=[]
                for i,(paragraph,expected_role) in enumerate(zip(paragraphs,roles)):
                    if not isinstance(paragraph,dict) or str(paragraph.get("role","")).strip().casefold()!=expected_role.casefold():
                        return self.send_json(502,{"error":"AI trả nhãn đoạn không đúng thứ tự IELTS ("+", ".join(roles)+"). Không áp dụng kết quả; hãy thử lại."})
                    idxs=paragraph.get("sentences")
                    if not isinstance(idxs,list) or not idxs:
                        return self.send_json(502,{"error":"Mỗi đoạn IELTS phải có ít nhất một câu; AI chưa phân đủ đoạn."})
                    try: groups.append([int(i)-1 for i in idxs])
                    except (TypeError,ValueError): return self.send_json(502,{"error":"AI trả về chỉ số câu không hợp lệ khi tách đoạn."})
                flat=[i for group in groups for i in group]
                if flat!=list(range(len(sentences))):
                    return self.send_json(502,{"error":"AI đã làm thiếu, lặp hoặc đảo thứ tự câu. Không áp dụng thay đổi; hãy thử lại."})
                return self.send_json(200,{"groups":groups,"roles":roles,"task":task,"model":model})
            except RuntimeError as e:
                return self.send_json(502,{"error":str(e)})
        if path == "/api/translation-sentence-feedback":
            user=self.session_user()
            if not user:return self.send_json(401,{"error":"Bạn cần đăng nhập trước khi chấm câu bằng AI."})
            sentence=data.get("sentence");answer=data.get("answer");question=data.get("question","");task=data.get("task","Task 2")
            if not isinstance(sentence,dict) or not isinstance(sentence.get("en"),str) or not isinstance(sentence.get("vi"),str) or not isinstance(answer,str) or not answer.strip() or len(answer)>6000:
                return self.send_json(400,{"error":"Cần có câu mẫu, bản dịch tiếng Việt và câu trả lời tiếng Anh hợp lệ."})
            system="""Bạn là gia sư luyện dịch ngược IELTS Writing. Chỉ đánh giá MỘT câu người học đã dịch từ tiếng Việt sang tiếng Anh. Không cho band IELTS và không yêu cầu sao chép nguyên văn bài mẫu. Chấp nhận cách diễn đạt tương đương nếu đúng ý, tự nhiên, chính xác và hợp văn phong học thuật. Phản hồi bằng tiếng Việt. So sánh ý nghĩa với câu tiếng Việt; câu tiếng Anh mẫu chỉ là một tham khảo, không phải cách viết duy nhất. Xác định các lỗi cụ thể về ngữ pháp, từ vựng, collocation, nghĩa hoặc tính tự nhiên; nếu câu đúng, không bịa lỗi. Trả JSON duy nhất: {\"status\":\"correct|minor_issue|error\",\"summary\":\"nhận xét ngắn\",\"strengths\":[\"...\"],\"errors\":[{\"original\":\"đoạn nguyên văn sai\",\"correction\":\"cách sửa\",\"explanation\":\"vì sao\"}],\"corrected_sentence\":\"bản sửa tối thiểu, giữ ý người học\"}. Mỗi original phải là một đoạn liên tục chính xác trong bài làm; nếu không lỗi thì errors là []. Hạn chế sửa toàn câu nếu chỉ có một lỗi nhỏ."""
            prompt="Đề bài: "+str(question)[:3000]+"\nTask: "+str(task)+"\nCâu nguồn tiếng Việt: "+sentence["vi"]+"\nCâu mẫu tiếng Anh (chỉ để đối chiếu): "+sentence["en"]+"\nCâu do người học viết: "+answer+"\nHãy chấm riêng câu này theo schema đã nêu."
            try:
                feedback,model=ai_generate(system,prompt,max_output_tokens=2200)
                if not isinstance(feedback,dict) or not isinstance(feedback.get("errors"),list) or feedback.get("status") not in ("correct","minor_issue","error"):
                    return self.send_json(502,{"error":"AI trả về nhận xét câu không đúng cấu trúc. Hãy thử lại."})
                feedback["errors"]=[e for e in feedback["errors"] if isinstance(e,dict)][:6]
                feedback["strengths"]=[str(x)[:500] for x in feedback.get("strengths",[]) if isinstance(x,str)][:4]
                return self.send_json(200,{"feedback":feedback,"model":model})
            except RuntimeError as e:
                return self.send_json(502,{"error":str(e)})
        if path == "/api/translate":
            sentences=data.get("sentences")
            if not isinstance(sentences,list) or not sentences or len(sentences)>60 or any(not isinstance(x,str) or not x.strip() or len(x)>2500 for x in sentences):
                return self.send_json(400, {"error":"Danh sách câu không hợp lệ. Mỗi lượt hỗ trợ tối đa 60 câu."})
            system="Bạn là biên dịch viên IELTS Writing Anh-Việt. Dịch chính xác, tự nhiên, đúng ngữ cảnh học thuật. Không thêm thông tin. Giữ nguyên số lượng và thứ tự câu. Trả về JSON hợp lệ theo schema {\"translations\":[\"bản dịch câu 1\", ...]} với đúng một bản dịch cho mỗi câu."
            prompt="Dịch các câu sau sang tiếng Việt. Không gộp/tách câu và chỉ trả về JSON theo schema đã nêu:\n"+json.dumps({"sentences":sentences},ensure_ascii=False)
            try:
                result,model=ai_generate(system,prompt,max_output_tokens=7000)
                translations=result.get("translations") if isinstance(result,dict) else None
                if not isinstance(translations,list) or len(translations)!=len(sentences) or any(not isinstance(x,str) or not x.strip() for x in translations):
                    # Retry only on structural mismatch, using smaller batches so the model is less likely to merge sentences.
                    translations=[]; models=[]; retry_failed=False
                    for start in range(0,len(sentences),5):
                        batch=sentences[start:start+5]
                        retry_prompt="Dịch từng câu sang tiếng Việt. Giữ nguyên đúng số câu và thứ tự; chỉ trả JSON {\"translations\":[...]}.\n"+json.dumps({"sentences":batch},ensure_ascii=False)
                        try:
                            retry_result,retry_model=ai_generate(system,retry_prompt,max_output_tokens=2200)
                            batch_translations=retry_result.get("translations") if isinstance(retry_result,dict) else None
                            if not isinstance(batch_translations,list) or len(batch_translations)!=len(batch) or any(not isinstance(x,str) or not x.strip() for x in batch_translations):
                                retry_failed=True;break
                            translations.extend(batch_translations);models.append(retry_model)
                        except RuntimeError:
                            retry_failed=True;break
                    if retry_failed or len(translations)!=len(sentences):
                        return self.send_json(502,{"error":"AI chưa giữ đúng số câu dù đã thử dịch theo nhóm 5 câu. Hãy dùng nút AI tách đoạn, rồi thử lại hoặc dịch từng đoạn ngắn hơn."})
                    model=" + ".join(dict.fromkeys(models)) or model
                return self.send_json(200,{"translations":translations,"model":model})
            except RuntimeError as e:
                code=503 if "GEMINI_API_KEY" in str(e) else (429 if "quota" in str(e).lower() else 502)
                return self.send_json(code,{"error":str(e)})
        if path == "/api/feedback":
            user = self.session_user()
            if not user:
                return self.send_json(401, {"error": "Bạn cần đăng nhập trước khi dùng AI chấm bài."})
            answers=data.get("answers"); essay=data.get("essay"); question=data.get("question","")
            if not isinstance(answers,list) or not isinstance(essay,list) or len(answers)!=len(essay) or not answers:
                return self.send_json(400,{"error":"Dữ liệu bài làm không hợp lệ."})
            if len(answers)>30 or any(not isinstance(a,str) or len(a)>12000 for a in answers):
                return self.send_json(400,{"error":"Câu trả lời không hợp lệ hoặc quá dài."})
            if any(not isinstance(item,dict) or not isinstance(item.get("vi"),str) or not isinstance(item.get("en"),str) for item in essay):
                return self.send_json(400,{"error":"Dữ liệu câu mẫu không hợp lệ."})
            prompt_data={"question":str(question)[:3000],"sentences":[{"number":i+1,"vietnamese":item["vi"],"model_answer":item["en"],"student_answer":answers[i] or "(Bỏ trống)"} for i,item in enumerate(essay)]}
            instructions='''Bạn là gia sư IELTS Writing. Phản hồi bằng tiếng Việt, chính xác và có tính sư phạm. Người học đang luyện dịch ngược từ tiếng Việt sang tiếng Anh. Đánh giá toàn bài một lần; không chấm điểm IELTS chính thức và không ép sao chép nguyên văn bài mẫu. Chấp nhận cách diễn đạt khác nếu đúng, tự nhiên và giữ nguyên ý. Phân biệt lỗi thật với khác biệt phong cách. Tập trung ý nghĩa, ngữ pháp, từ vựng/collocation, độ tự nhiên và văn phong học thuật.
Chỉ trả về JSON object hợp lệ theo schema:
{"overall_summary":"...","strengths":["..."],"sentence_feedback":[{"sentence_number":1,"student_answer":"...","status":"correct|minor_issue|error|blank","issue":"...","explanation":"...","explanation_points":["Lý do 1","Lý do 2"],"incorrect_segments":["đoạn sai chính xác trong câu người học viết"],"suggested_revision":"...","error_type":"Grammar|Vocabulary|Collocation|Meaning|Naturalness|None"}],"recurring_errors":[{"title":"...","explanation":"...","example":"...","practice":"...","answer":"..."}],"exercises":[{"question":"...","answer":"..."}],"improvement_plan":["..."]}
YÊU CẦU QUAN TRỌNG VỀ HIỂN THỊ LỖI: với mỗi câu, incorrect_segments phải là mảng gồm các đoạn NGUYÊN VĂN, liên tục, được chép chính xác từ student_answer và chỉ chứa phần sai; không đưa cả câu nếu chỉ sai vài từ. Không đánh dấu phần đúng chỉ vì khác bài mẫu. Nếu không có lỗi, dùng []. explanation_points phải là mảng 1–4 gạch đầu dòng ngắn bằng tiếng Việt, mỗi ý giải thích một lỗi/điểm cần lưu ý. suggested_revision phải sửa tối thiểu cần thiết và giữ nguyên ý người học. Giới hạn strengths tối đa 4, recurring_errors tối đa 5, exercises tối đa 3, improvement_plan tối đa 4. sentence_feedback phải có đúng một mục cho mỗi câu, giữ đúng số câu. Nếu câu diễn đạt đúng, status là correct, incorrect_segments là [] và giải thích ngắn gọn.'''
            try:
                feedback,model=ai_generate(instructions,"Hãy phân tích bài làm sau và trả về JSON theo schema yêu cầu:\n"+json.dumps(prompt_data,ensure_ascii=False),max_output_tokens=7000)
                if not isinstance(feedback,dict) or not isinstance(feedback.get("sentence_feedback"),list):
                    return self.send_json(502,{"error":"Phản hồi Gemini không đúng cấu trúc. Hãy thử lại hoặc dùng prompt thủ công."})
                return self.send_json(200,{"feedback":feedback,"model":model})
            except RuntimeError as e:
                message=str(e); code=503 if "GEMINI_API_KEY" in message else (429 if "quota" in message.lower() else 502)
                return self.send_json(code,{"error":message})
        if path == "/api/brainstorm-review":
            user = self.session_user()
            if not user:
                return self.send_json(401, {"error": "Bạn cần đăng nhập trước khi dùng AI Brainstorm."})
            task = str(data.get("task", "Task 2"))[:20]
            question = data.get("question", "")
            mindmap = data.get("mindmap", {})
            if task not in ("Task 1", "Task 2") or not isinstance(question, str) or not question.strip() or len(question) > 5000 or not isinstance(mindmap, dict):
                return self.send_json(400, {"error": "Đề bài hoặc mindmap không hợp lệ."})
            def flat_map(obj, prefix=""):
                out={}
                if isinstance(obj,dict):
                    for k,v in obj.items():
                        if k in ("corrections","filled","reviewSummary","reviewedAt","schemaVersion","expansion"): continue
                        path=f"{prefix}.{k}" if prefix else k
                        if isinstance(v,dict): out.update(flat_map(v,path))
                        elif isinstance(v,str): out[path]=v[:2500]
                return out
            fields=flat_map(mindmap)
            # Only analyze the currently selected secondary branch (Example OR Explain 2), not hidden alternatives.
            for body_name in ("body1","body2"):
                body_value=mindmap.get(body_name,{}) if isinstance(mindmap,dict) else {}
                if not isinstance(body_value,dict): continue
                for idea_name in ("idea1","idea2"):
                    idea_value=body_value.get(idea_name,{})
                    if isinstance(idea_value,dict):
                        prefix=f"{body_name}.{idea_name}."
                        if idea_value.get("expansion","example")=="example": fields.pop(prefix+"explain2",None)
                        else: fields.pop(prefix+"example",None)
            if len(fields)>30:
                return self.send_json(400, {"error": "Mindmap có quá nhiều trường."})
            criterion = "Task Achievement (TA)" if task == "Task 1" else "Task Response (TR)"
            system = f"""Bạn là huấn luyện viên IELTS Writing chuyên đánh giá dàn ý sơ bộ, không phải bài luận hoàn chỉnh. Trả lời tiếng Việt, không chấm band cho mindmap và KHÔNG chấm gắt. Đánh giá ý tưởng theo khía cạnh liên quan đề, độ bao phủ yêu cầu, mức phát triển/sâu sắc và tính hỗ trợ cho lập trường; dùng nguyên tắc {criterion} trong IELTS Writing band descriptors. Với Task 1, xem xét khả năng chọn/nhóm đặc điểm chính, overview và tính chính xác của dữ liệu. Với Task 2, xem xét trả lời đủ các phần của câu hỏi, lập trường rõ, ý chính liên quan và được phát triển hợp lý. Vì đây là bản nháp, hãy chấp nhận viết tắt, cụm từ, tiếng Việt xen tiếng Anh, mũi tên, bullet và câu chưa hoàn chỉnh; KHÔNG trừ điểm vì ngữ pháp, chính tả hoặc thiếu câu hoàn chỉnh trừ khi làm ý nghĩa không rõ. Không tự bịa số liệu Task 1. Không biến nhận xét thành bài giảng dài. Nếu một ô có ý tưởng đúng, giữ nguyên. Chỉ đề xuất sửa nhẹ khi ý sai, mơ hồ, trùng lặp, nông hoặc chưa trả lời đề. Nếu ô trống, hãy tự gợi ý một ý ngắn gọn phù hợp; không yêu cầu người dùng điền lại đề. Đề bài là nguồn sự thật. Trả JSON duy nhất theo schema: {{"summary":"Tối đa 2 câu tóm tắt ưu tiên liên quan đề/độ sâu/độ bao phủ.","fill_blanks":{{"body1.idea1.content":"gợi ý ý chính","body1.idea1.explain1":"giải thích 1","body1.idea1.example":"ví dụ hoặc explain2 theo trường đang chọn",...}},"corrections":{{"body1.idea2.content":{{"corrected":"bản sửa ngắn","reason":"giải thích ngắn gọn bằng tiếng Việt"}}}}}}. Chỉ đưa vào fill_blanks những trường được cung cấp nhưng đang trống. Chỉ đưa vào corrections trường đã có nội dung và thực sự cần chỉnh; corrected phải giữ ý gốc tối đa có thể. Không trả trường không có trong dữ liệu."""
            prompt = "Rà soát mindmap nháp này. Dùng TA/TR band descriptors chỉ cho relevance, coverage and development; tuyệt đối không chấm ngữ pháp của bản nháp.\n" + json.dumps({"task":task,"question":question,"question_type":str(data.get("type",""))[:100],"topic":str(data.get("topic",""))[:100],"mindmap":fields},ensure_ascii=False)
            try:
                review, model = ai_generate(system, prompt, max_output_tokens=5000)
                if not isinstance(review, dict) or not isinstance(review.get("fill_blanks",{}),dict) or not isinstance(review.get("corrections",{}),dict):
                    return self.send_json(502, {"error": "Gemini trả về cấu trúc Brainstorm không hợp lệ. Hãy thử lại."})
                allowed=set(fields)
                review["fill_blanks"]={k:str(v)[:1800] for k,v in review.get("fill_blanks",{}).items() if k in allowed and not fields.get(k," ").strip() and isinstance(v,str) and v.strip()}
                clean={}
                for k,v in review.get("corrections",{}).items():
                    if k in allowed and fields.get(k," ").strip() and isinstance(v,dict) and isinstance(v.get("corrected"),str) and v["corrected"].strip():
                        clean[k]={"corrected":v["corrected"][:1800],"reason":str(v.get("reason",""))[:500],"original":fields[k]}
                review["corrections"]=clean
                review["summary"]=str(review.get("summary",""))[:1200]
                return self.send_json(200,{"review":review,"model":model,"criterion":criterion,"rubricFocus":"idea relevance, coverage, development"})
            except RuntimeError as e:
                message=str(e); code=503 if "GEMINI_API_KEY" in message else (429 if "quota" in message.lower() else 502)
                return self.send_json(code,{"error":message})
        if path == "/api/lesson-suggestions":
            user=self.session_user()
            if not user:return self.send_json(401,{"error":"Đăng nhập để dùng AI tạo gợi ý."})
            task=str(data.get("task","Task 2"))[:20]; title=str(data.get("title",""))[:180]; question=str(data.get("question",""))[:5000]; kind=str(data.get("type",""))[:100]; topic=str(data.get("topic",""))[:100]; english=str(data.get("english",""))[:12000]
            if task not in ("Task 1","Task 2") or not english.strip():return self.send_json(400,{"error":"Chọn Task và nhập bài mẫu tiếng Anh trước khi tạo gợi ý."})
            system="Bạn là trợ lý biên soạn tài liệu luyện IELTS Writing. Trả JSON tiếng Việt, không bịa nguồn hay điểm band. Với từng câu tiếng Anh, tạo một gợi ý ngắn về từ vựng/cấu trúc để người học tự dịch; không viết lại toàn câu thành đáp án. Trả schema {hints:[string], title_suggestion:string, question_review:string}. Mỗi câu một gợi ý theo đúng thứ tự."
            prompt=json.dumps({"task":task,"title":title,"type":kind,"topic":topic,"question":question,"english_sentences":[x.strip() for x in english.splitlines() if x.strip()]},ensure_ascii=False)
            try:
                result,model=ai_generate(system,prompt, max_output_tokens=4500)
                return self.send_json(200,{"suggestions":result,"model":model})
            except RuntimeError as e:return self.send_json(502,{"error":str(e)})
        if path == "/api/feedback-corrections":
            user=self.session_user()
            if not user:return self.send_json(401,{"error":"Hãy đăng nhập trước khi lưu hiệu chỉnh."})
            payload=data.get("payload")
            if not isinstance(payload,dict):return self.send_json(400,{"error":"Thiếu dữ liệu hiệu chỉnh."})
            bands=payload.get("corrected_bands",{})
            if not isinstance(bands,dict) or not bands or any(not isinstance(v,(int,float)) or v<0 or v>9 for v in bands.values()):
                return self.send_json(400,{"error":"Band hiệu chỉnh phải là object các điểm số từ 0 đến 9."})
            serialized=json.dumps(payload,ensure_ascii=False)
            if len(serialized)>200000:return self.send_json(413,{"error":"Dữ liệu hiệu chỉnh quá lớn."})
            cid="corr-"+secrets.token_urlsafe(12); now=int(time.time())
            with db() as c:c.execute("INSERT INTO feedback_corrections(id,user_id,payload_json,status,created_at) VALUES(?,?,?,?,?)",(cid,user["id"],serialized,"pending_review" if user.get("role")=="admin" else "private",now))
            return self.send_json(200,{"ok":True,"id":cid,"scope":"private" if user.get("role")!="admin" else "admin-review"})
        if path == "/api/admin/calibrations":
            admin=self.require_admin()
            if not admin:return
            payload=data.get("payload")
            bands=payload.get("corrected_bands") if isinstance(payload,dict) else None
            if not isinstance(payload,dict) or payload.get("task") not in ("Task 1","Task 2") or not isinstance(bands,dict) or not bands or any(not isinstance(v,(int,float)) or v<0 or v>9 for v in bands.values()):return self.send_json(400,{"error":"Cần Task hợp lệ và band số từ 0 đến 9 đã được admin xác nhận."})
            cid="cal-"+secrets.token_urlsafe(12); now=int(time.time())
            with db() as c:c.execute("INSERT INTO admin_calibrations(id,payload_json,created_by,created_at,active) VALUES(?,?,?,?,1)",(cid,json.dumps(payload,ensure_ascii=False),admin["username"],now))
            return self.send_json(200,{"ok":True,"id":cid})
        if path == "/api/admin/references":
            admin=self.require_admin()
            if not admin:return
            payload=data.get("reference")
            if not isinstance(payload,dict) or not str(payload.get("title"," ")).strip() or not str(payload.get("url"," ")).startswith("https://"):return self.send_json(400,{"error":"Nguồn cần tiêu đề và URL HTTPS."})
            rid="ref-"+secrets.token_urlsafe(10); payload["id"]=rid; now=int(time.time())
            with db() as c:c.execute("INSERT INTO reference_overrides(id,payload_json,deleted,updated_at,updated_by) VALUES(?,?,0,?,?)",(rid,json.dumps(payload,ensure_ascii=False),now,admin["username"]))
            return self.send_json(200,{"ok":True,"reference":payload})
        if path == "/api/admin/promote-correction":
            admin=self.require_admin()
            if not admin:return
            cid=str(data.get("id",""))
            with db() as c: row=c.execute("SELECT payload_json FROM feedback_corrections WHERE id=?",(cid,)).fetchone()
            if not row:return self.send_json(404,{"error":"Không tìm thấy hiệu chỉnh."})
            payload=json.loads(row["payload_json"])
            cal={k:payload.get(k) for k in ("task","question","essay_excerpt","corrected_bands","reasoning") if payload.get(k) not in (None,"",[])}
            if not cal.get("task") or not cal.get("corrected_bands"):return self.send_json(400,{"error":"Hiệu chỉnh thiếu Task hoặc band đã xác nhận nên chưa thể đưa vào hiệu chuẩn chung."})
            new_id="cal-"+secrets.token_urlsafe(12); now=int(time.time())
            with db() as c:
                c.execute("INSERT INTO admin_calibrations(id,payload_json,created_by,created_at,active) VALUES(?,?,?,?,1)",(new_id,json.dumps(cal,ensure_ascii=False),admin["username"],now))
                c.execute("UPDATE feedback_corrections SET status='approved',reviewed_by=? WHERE id=?",(admin["username"],cid))
            return self.send_json(200,{"ok":True,"id":new_id})
        if path == "/api/writing-feedback":
            user = self.session_user()
            if not user:
                return self.send_json(401, {"error": "Bạn cần đăng nhập trước khi dùng AI chấm bài Writing."})
            task = str(data.get("task", "Task 2"))[:20]
            question = data.get("question", "")
            essay_text = data.get("essay_text", "")
            if task not in ("Task 1", "Task 2"):
                return self.send_json(400, {"error": "Task phải là Task 1 hoặc Task 2."})
            if not isinstance(question, str) or not question.strip() or len(question) > 5000:
                return self.send_json(400, {"error": "Đề bài không hợp lệ."})
            if not isinstance(essay_text, str) or len(essay_text.strip()) < 30 or len(essay_text) > 20000:
                return self.send_json(400, {"error": "Bài viết quá ngắn hoặc quá dài. Hãy nhập ít nhất 30 ký tự và tối đa 20.000 ký tự."})
            word_count = len(re.findall(r"\b[\w’'-]+\b", essay_text, flags=re.UNICODE))
            rubric = build_scoring_rubric(task)
            task_note = "Task 1 Academic" if task == "Task 1" else "Task 2"
            image_data=data.get("image_data") if isinstance(data.get("image_data"),str) else ""
            image_note = "\n\nĐÃ ĐÍNH KÈM ẢNH ĐỀ TASK 1: hãy quan sát biểu đồ/bản đồ trực tiếp, xác định đúng đơn vị, hạng mục và số liệu trước khi đánh giá mọi câu mô tả. Nếu ảnh không hiển thị rõ, nói rõ giới hạn; tuyệt đối không bịa số liệu." if task == "Task 1" and image_data.startswith("data:image/") else ("\n\nKHÔNG CÓ ẢNH ĐỀ ĐƯỢC GỬI: không được khẳng định đã đối chiếu số liệu trực quan; chỉ đánh giá ngôn ngữ và nội dung dựa trên đề chữ." if task == "Task 1" else "")
            prompt = "Hãy chấm bài theo rubric và JSON schema trong system instruction.\nTASK: " + task_note + "\nĐỀ BÀI:\n" + question.strip() + "\n\nBÀI VIẾT NGUYÊN VĂN:\n" + essay_text.strip() + f"\n\nSố từ tự đếm phía máy chủ: {word_count}. Hãy tự kiểm tra lại nếu cần." + image_note
            try:
                feedback, model = ai_generate(rubric, prompt, max_output_tokens=9000, image_data=data.get("image_data"))
                criteria = feedback.get("criteria") if isinstance(feedback, dict) else None
                if not isinstance(feedback, dict) or not isinstance(criteria, list) or len(criteria) != 4:
                    return self.send_json(502, {"error": "Gemini trả về cấu trúc chấm bài không hợp lệ. Hãy thử lại."})
                bands = []
                for criterion in criteria:
                    try:
                        band = float(criterion.get("band"))
                    except (AttributeError, TypeError, ValueError):
                        return self.send_json(502, {"error": "Gemini chưa trả đủ điểm số cho bốn tiêu chí. Hãy thử lại."})
                    if band < 0 or band > 9:
                        return self.send_json(502, {"error": "Điểm tiêu chí do AI trả về nằm ngoài thang 0–9."})
                    criterion["band"] = max(0, min(9, int(band + 0.5)))
                    bands.append(criterion["band"])
                # IELTS Writing reports the average of the four criteria rounded to the nearest half band.
                feedback["overall_band"] = min(9.0, max(0.0, int(sum(bands) / 4 * 2 + 0.5) / 2))
                feedback["word_count"] = word_count
                feedback["task_criterion"] = "TA" if task == "Task 1" else "TR"
                if task == "Task 1" and criteria:
                    if "response" in str(criteria[0].get("name", "")).lower():
                        criteria[0]["name"] = "Task Achievement"
                elif task == "Task 2" and criteria:
                    if "achievement" in str(criteria[0].get("name", "")).lower():
                        criteria[0]["name"] = "Task Response"
                return self.send_json(200, {"feedback": feedback, "model": model, "word_count": word_count, "image_received": bool(task == "Task 1" and image_data.startswith("data:image/"))})
            except RuntimeError as e:
                message = str(e)
                code = 503 if "GEMINI_API_KEY" in message else (429 if "quota" in message.lower() else 502)
                return self.send_json(code, {"error": message})
        if path == "/api/logout":
            user = self.session_user()
            if user:
                # Delete only the session token from this browser.
                jar = cookies.SimpleCookie()
                try:
                    jar.load(self.headers.get("Cookie", ""))
                    token = jar[COOKIE_NAME].value if COOKIE_NAME in jar else None
                except cookies.CookieError:
                    token = None
                if token:
                    with db() as c:
                        c.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(token),))
            return self.send_json(200, {"ok": True}, self.clear_session())
        return self.send_json(404, {"error": "Không tìm thấy API."})

    def do_DELETE(self):
        path = urlparse(self.path).path
        if not self.gate_user(): return self.send_json(423, {"error": "Nhập mật khẩu truy cập chung để mở WriteWiser."})
        override_match = re.fullmatch(r"/api/library-overrides/([^/]+)", path)
        if override_match:
            admin=self.require_admin()
            if not admin:return
            lesson_id=override_match.group(1); now=int(time.time())
            with db() as c:c.execute("INSERT INTO library_overrides(id,lesson_json,deleted,updated_at,updated_by) VALUES(?,NULL,1,?,?) ON CONFLICT(id) DO UPDATE SET deleted=1,updated_at=excluded.updated_at,updated_by=excluded.updated_by",(lesson_id,now,admin["username"]))
            return self.send_json(200,{"ok":True})
        personal_match = re.fullmatch(r"/api/personal-lessons/([^/]+)", path)
        if personal_match:
            user=self.session_user()
            if not user:return self.send_json(401,{"error":"Bạn cần đăng nhập."})
            with db() as c:
                row=c.execute("SELECT state_json FROM user_state WHERE user_id=?",(user["id"],)).fetchone()
                if row:
                    state=json.loads(row["state_json"]); state["customLessons"]=[x for x in state.get("customLessons",[]) if x.get("id")!=personal_match.group(1)]
                    c.execute("UPDATE user_state SET state_json=?,updated_at=? WHERE user_id=?",(json.dumps(state,ensure_ascii=False),int(time.time()),user["id"]))
            return self.send_json(200,{"ok":True})
        ref_match=re.fullmatch(r"/api/admin/references/([^/]+)",path)
        if ref_match:
            admin=self.require_admin()
            if not admin:return
            now=int(time.time())
            with db() as c:c.execute("INSERT INTO reference_overrides(id,payload_json,deleted,updated_at,updated_by) VALUES(?,NULL,1,?,?) ON CONFLICT(id) DO UPDATE SET deleted=1,updated_at=excluded.updated_at,updated_by=excluded.updated_by",(ref_match.group(1),now,admin["username"]))
            return self.send_json(200,{"ok":True})
        cal_match=re.fullmatch(r"/api/admin/calibrations/([^/]+)",path)
        if cal_match:
            admin=self.require_admin()
            if not admin:return
            with db() as c:c.execute("UPDATE admin_calibrations SET active=0 WHERE id=?",(cal_match.group(1),))
            return self.send_json(200,{"ok":True})
        match = re.fullmatch(r"/api/global-lessons/([^/]+)", path)
        if not match: return self.send_json(404, {"error": "Không tìm thấy API."})
        admin = self.require_admin()
        if not admin: return
        with db() as c:
            c.execute("DELETE FROM global_lessons WHERE id=?", (match.group(1),))
        return self.send_json(200, {"ok": True})

    def do_PUT(self):
        path = urlparse(self.path).path
        if not self.gate_user(): return self.send_json(423, {"error": "Nhập mật khẩu truy cập chung để mở WriteWiser."})
        if path != "/api/state":
            return self.send_json(404, {"error": "Không tìm thấy API."})
        user = self.session_user()
        if not user:
            return self.send_json(401, {"error": "Bạn cần đăng nhập."})
        try:
            data = self.read_json()
        except ValueError as e:
            return self.send_json(400, {"error": str(e)})
        state = data.get("state")
        if not isinstance(state, dict):
            return self.send_json(400, {"error": "Thiếu dữ liệu học tập hợp lệ."})
        # Keep storage bounded and accept only expected top-level fields.
        state = {k: state.get(k) for k in ("profile", "answers", "lessonAnswers", "independentDrafts", "brainstormDrafts", "currentLessonId", "history", "mistakes", "customLessons", "studyDraft") if k in state}
        # Keep only small, non-sensitive resume metadata; all draft contents remain
        # in the account-scoped user_state payload above.
        draft = state.get("studyDraft")
        if isinstance(draft, dict):
            lesson_id = draft.get("lessonId")
            mode = draft.get("practiceMode")
            timer_mode = draft.get("timerMode")
            def bounded_int(value, default, maximum):
                try: parsed = int(value)
                except (TypeError, ValueError, OverflowError): parsed = default
                return max(0, min(maximum, parsed))
            state["studyDraft"] = {
                "version": 1,
                "lessonId": lesson_id[:200] if isinstance(lesson_id, str) else "",
                "practiceMode": mode if mode in ("reverse", "independent") else "reverse",
                "activeSentenceIndex": bounded_int(draft.get("activeSentenceIndex", 0), 0, 500),
                "timerMode": timer_mode if timer_mode in ("practice", "exam") else "practice",
                "examDuration": bounded_int(draft.get("examDuration", 0), 0, 240),
                "timerElapsedSeconds": bounded_int(draft.get("timerElapsedSeconds", 0), 0, 86400),
                "brainstormOpen": bool(draft.get("brainstormOpen", False)),
                "savedAt": str(draft.get("savedAt", ""))[:40]
            }
        else:
            state.pop("studyDraft", None)
        serialized = json.dumps(state, ensure_ascii=False)
        if len(serialized.encode("utf-8")) > 1_500_000:
            return self.send_json(413, {"error": "Dữ liệu học tập vượt quá giới hạn lưu trữ."})
        now = int(time.time())
        with db() as c:
            c.execute("""INSERT INTO user_state(user_id,state_json,updated_at) VALUES(?,?,?)
                         ON CONFLICT(user_id) DO UPDATE SET state_json=excluded.state_json, updated_at=excluded.updated_at""",
                      (user["id"], serialized, now))
        return self.send_json(200, {"ok": True, "updatedAt": now})

    def do_HEAD(self):
        return super().do_HEAD()

def main():
    os.chdir(BASE)
    init_db()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"WriteWiser v32 running at http://{HOST}:{PORT} ({'PostgreSQL' if DATABASE_URL else 'SQLite local'})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping WriteWiser v30.")
    finally:
        server.server_close()

if __name__ == "__main__":
    main()
