#!/usr/bin/env python3
"""WriteWiser backend: local SQLite for development, PostgreSQL via DATABASE_URL for cloud hosting."""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, unquote
from http import cookies
import hashlib, hmac, json, os, posixpath, secrets, sqlite3, time, re, urllib.request, urllib.error, base64, io, zipfile, csv, xml.etree.ElementTree as ET, html
from pathlib import Path
from html.parser import HTMLParser
import threading
from datetime import datetime

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "draftwise.sqlite3"

# Public, crawlable landing routes. Account/history/admin screens remain noindex
# and are deliberately excluded from the sitemap.
SEO_PAGES = {
    "/": {
        "view": "practice", "mode": "reverse", "title": "WriteWiser – Luyện IELTS Writing miễn phí, AI chấm Writing",
        "description": "WriteWiser hỗ trợ luyện IELTS Writing miễn phí với thư viện đề, luyện dịch ngược Anh–Việt, tự viết bài và nhận phản hồi AI theo 4 tiêu chí IELTS.",
        "heading": "Luyện IELTS Writing miễn phí cùng WriteWiser",
        "summary": "WriteWiser là không gian luyện IELTS Writing với thư viện đề, dịch ngược từng câu, chế độ tự viết, Brainstorm và phản hồi AI dựa trên các tiêu chí Task Achievement/Response, Coherence and Cohesion, Lexical Resource và Grammatical Range and Accuracy.",
        "bullets": ["Luyện dịch ngược từng câu để chủ động diễn đạt bằng tiếng Anh.", "Tự viết Task 1 và Task 2, nhận phản hồi AI để biết điểm mạnh và lỗi cần ưu tiên.", "Dùng Brainstorm để phát triển ý trước khi viết bài hoàn chỉnh."],
        "index": True,
    },
    "/thu-vien-de-ielts-writing": {
        "view": "library", "mode": "reverse", "title": "Thư viện đề IELTS Writing miễn phí | WriteWiser",
        "description": "Khám phá thư viện đề IELTS Writing Task 1 và Task 2 của WriteWiser. Tìm đề theo dạng bài, chủ đề và nguồn; luyện dịch ngược hoặc tự viết.",
        "heading": "Thư viện đề IELTS Writing Task 1 và Task 2",
        "summary": "Thư viện đề WriteWiser giúp người học tìm bài luyện IELTS Writing theo Task 1, Task 2, dạng câu hỏi và chủ đề. Bạn có thể luyện dịch ngược từng câu hoặc chuyển sang chế độ tự viết. Các liên kết bài mẫu bên ngoài dẫn về nguồn gốc để người học kiểm tra nội dung đầy đủ.",
        "bullets": ["Task 1: biểu đồ đường, cột, tròn, bảng, quy trình, bản đồ và biểu đồ kết hợp.", "Task 2: Opinion, Discussion, Advantages/Disadvantages, Causes/Solutions và Two-part Question.", "Lọc danh mục theo Task, dạng bài, chủ đề và nguồn tham khảo."],
        "index": True,
    },
    "/luyen-dich-nguoc-tieng-anh": {
        "view": "practice", "mode": "reverse", "title": "Luyện dịch ngược Anh–Việt cho IELTS Writing | WriteWiser",
        "description": "Luyện dịch ngược IELTS Writing với WriteWiser: đọc ý tiếng Việt, tự viết lại câu tiếng Anh, nhận gợi ý sửa và theo dõi tiến độ từng câu.",
        "heading": "Luyện dịch ngược tiếng Anh cho IELTS Writing",
        "summary": "Chế độ luyện dịch ngược của WriteWiser hiển thị bản dịch tiếng Việt theo từng câu để bạn tự diễn đạt lại bằng tiếng Anh. Người học có thể chọn câu đang luyện, lưu tiến độ và yêu cầu AI nhận xét câu về ý nghĩa, ngữ pháp, collocation và độ tự nhiên.",
        "bullets": ["Tập trung vào từng câu thay vì chỉ chép lại cả bài mẫu.", "Đối chiếu câu tự viết với ý gốc và xem gợi ý cải thiện.", "Phù hợp để luyện cách diễn đạt, cấu trúc câu và vốn từ học thuật."],
        "index": True,
    },
    "/ai-cham-ielts-writing": {
        "view": "practice", "mode": "independent", "title": "AI chấm IELTS Writing miễn phí – góp ý theo 4 tiêu chí | WriteWiser",
        "description": "Dùng WriteWiser để nhận phản hồi AI cho IELTS Writing Task 1 và Task 2, kèm ước lượng band, phân tích từng tiêu chí và đề xuất sửa bài.",
        "heading": "AI chấm IELTS Writing Task 1 và Task 2",
        "summary": "WriteWiser hỗ trợ nhận xét bài Writing theo bốn tiêu chí thường dùng trong IELTS Writing: Task Achievement/Task Response, Coherence and Cohesion, Lexical Resource và Grammatical Range and Accuracy. Phản hồi tập trung vào dẫn chứng từ bài viết, lỗi cần ưu tiên và cách nâng cấp câu/đoạn; điểm AI là ước lượng học tập, không phải kết quả chính thức từ IELTS.",
        "bullets": ["Phân tích riêng bốn tiêu chí thay vì chỉ đưa một điểm tổng.", "Nêu dẫn chứng, vấn đề cần cải thiện và gợi ý sửa cụ thể.", "Dùng phản hồi để xác định bước luyện tiếp theo cho Task 1 hoặc Task 2."],
        "index": True,
    },
    "/luyen-brainstorm-ielts-writing": {
        "view": "practice", "mode": "reverse", "brainstorm": True, "title": "Luyện Brainstorm IELTS Writing – phát triển ý trước khi viết | WriteWiser",
        "description": "Luyện Brainstorm IELTS Writing bằng mindmap: phát triển Idea, Explain, Example hoặc Explain 2 cho hai đoạn thân bài trước khi viết.",
        "heading": "Luyện Brainstorm IELTS Writing bằng mindmap",
        "summary": "Công cụ Brainstorm của WriteWiser giúp tổ chức ý tưởng cho IELTS Writing Task 2 theo cấu trúc từng đoạn thân bài. Người học phát triển Idea, Explain và Example hoặc Explain 2, sau đó có thể yêu cầu AI rà soát mức độ rõ ràng, liên quan và cách phát triển lập luận trước khi bắt đầu viết.",
        "bullets": ["Tách luận điểm và phần giải thích để tránh nêu ý quá ngắn.", "Phát triển từng ý bằng ví dụ hoặc một bước giải thích bổ sung.", "Nhận góp ý để hoàn thiện dàn ý trước khi viết bài hoàn chỉnh."],
        "index": True,
    },
    "/lich-su-bai-lam": {
        "view": "history", "title": "Lịch sử bài làm cá nhân | WriteWiser", "description": "Xem lịch sử luyện IELTS Writing và các phản hồi đã lưu trong tài khoản WriteWiser.", "heading": "Lịch sử bài làm cá nhân", "summary": "Khu vực riêng tư để xem bài làm và phản hồi đã lưu.", "bullets": [], "index": False,
    },
    "/dashboard-hoc-tap": {
        "view": "dashboard", "title": "Dashboard học tập cá nhân | WriteWiser", "description": "Theo dõi tiến độ và thống kê luyện IELTS Writing trong tài khoản WriteWiser.", "heading": "Dashboard học tập cá nhân", "summary": "Khu vực thống kê riêng của người học.", "bullets": [], "index": False,
    },
    "/so-tay-loi-sai": {
        "view": "mistakes", "title": "Sổ tay lỗi sai cá nhân | WriteWiser", "description": "Ôn tập các lỗi thường gặp đã lưu trong quá trình luyện IELTS Writing trên WriteWiser.", "heading": "Sổ tay lỗi sai cá nhân", "summary": "Khu vực ôn tập riêng của tài khoản.", "bullets": [], "index": False,
    },
    "/so-tay-ghi-chu-ai": {
        "view": "vocabularyNotes", "title": "Sổ tay ghi chú AI | WriteWiser", "description": "Lưu từ vựng, cụm từ và cấu trúc IELTS Writing với nghĩa tiếng Việt, cách dùng và ví dụ do AI hỗ trợ.", "heading": "Sổ tay ghi chú AI", "summary": "Khu vực ghi chú học tập riêng.", "bullets": [], "index": False,
    },
    "/cai-dat-tai-khoan": {
        "view": "settings", "title": "Cài đặt tài khoản | WriteWiser", "description": "Quản lý cài đặt tài khoản WriteWiser.", "heading": "Cài đặt tài khoản", "summary": "Khu vực riêng tư của tài khoản.", "bullets": [], "index": False,
    },
    "/quan-ly-de-cua-toi": {
        "view": "personalLessons", "title": "Quản lý đề riêng | WriteWiser", "description": "Quản lý bộ đề IELTS Writing đã lưu trong tài khoản WriteWiser.", "heading": "Quản lý đề riêng", "summary": "Khu vực riêng tư của tài khoản.", "bullets": [], "index": False,
    },
    "/quan-ly-thu-vien": {
        "view": "adminLessons", "title": "Quản lý thư viện | WriteWiser", "description": "Khu vực quản trị thư viện WriteWiser.", "heading": "Quản lý thư viện", "summary": "Khu vực quản trị; không dành cho lập chỉ mục công khai.", "bullets": [], "index": False,
    },
    "/bao-mat-web": {
        "view": "adminSettings", "title": "Bảo mật website | WriteWiser", "description": "Khu vực quản trị bảo mật WriteWiser.", "heading": "Bảo mật website", "summary": "Khu vực quản trị; không dành cho lập chỉ mục công khai.", "bullets": [], "index": False,
    },
}
PUBLIC_SITEMAP_PATHS = ["/", "/thu-vien-de-ielts-writing", "/luyen-dich-nguoc-tieng-anh", "/ai-cham-ielts-writing", "/luyen-brainstorm-ielts-writing"]
SEO_NAV_LINKS = [
    ("Thư viện đề IELTS Writing", "/thu-vien-de-ielts-writing"),
    ("Luyện dịch ngược Anh–Việt", "/luyen-dich-nguoc-tieng-anh"),
    ("AI chấm IELTS Writing", "/ai-cham-ielts-writing"),
    ("Luyện Brainstorm IELTS Writing", "/luyen-brainstorm-ielts-writing"),
]

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


from ww_db import (
    SQLiteSession, PostgresSession, db, is_integrity_error,
    maybe_prune_expired_sessions, _history_created_epoch,
    upsert_user_history, read_user_history, init_db,
    password_hash, token_hash, read_setting, write_setting,
    store_password, verify_stored_password, ensure_initial_security_accounts,
    invalidate_leaderboard_cache, compute_cached_leaderboard,
)
from ww_importer import (
    DolLinkParser, fetch_dol_catalog, TextOnlyParser,
    extract_docx_text, infer_import_sections, parse_import_file,
)
from ww_scoring import (
    load_scoring_resources, build_scoring_rubric, _as_json_list,
    normalize_writing_feedback, _number_band, _criterion_key,
    _summarize_writing_history,
)
from ww_ai import (
    ALLOWED_IMAGE_HOSTS, fetch_remote_or_local_image, fetch_task1_image_as_data_url,
    gemini_generate, custom_openai_compatible_providers,
    parse_json_response_content, openai_compatible_generate, ai_generate,
)

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
        maybe_prune_expired_sessions()
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

    def public_origin(self):
        configured_base = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
        if configured_base:
            parsed_base = urlparse(configured_base)
            if parsed_base.scheme in ("http", "https") and parsed_base.netloc and not parsed_base.username and not parsed_base.password:
                return f"{parsed_base.scheme}://{parsed_base.netloc}"
        host = (self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or "localhost:8000").split(",")[0].strip()
        if not re.fullmatch(r"[A-Za-z0-9.\-\[\]:]+", host):
            host = "localhost:8000"
        proto = (self.headers.get("X-Forwarded-Proto") or ("http" if host.startswith(("localhost", "127.0.0.1")) else "https")).split(",")[0].strip().lower()
        if proto not in ("http", "https"):
            proto = "https"
        return f"{proto}://{host}"

    def build_sitemap(self):
        origin = self.public_origin()
        urls = "\n".join(f"  <url><loc>{html.escape(origin + p, quote=True)}</loc></url>" for p in PUBLIC_SITEMAP_PATHS)
        return '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + urls + "\n</urlset>\n"

    def send_text_response(self, status, content_type, body, cache="public, max-age=300"):
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def render_seo_gate_content(self, page):
        heading = html.escape(page.get("heading", "WriteWiser"))
        summary = html.escape(page.get("summary", ""))
        bullets = page.get("bullets", [])
        bullet_html = "" if not bullets else "<ul class=\"seo-points\">" + "".join(f"<li>{html.escape(x)}</li>" for x in bullets) + "</ul>"
        links = "<nav class=\"seo-links\" aria-label=\"Các tính năng WriteWiser\">" + "".join(f'<a href="{html.escape(path, quote=True)}">{html.escape(label)}</a>' for label,path in SEO_NAV_LINKS if path != self.clean_route_path()) + "</nav>"
        return f'<div class="seo-kicker">WRITEWISER · IELTS WRITING</div><h2>{heading}</h2><p>{summary}</p>{bullet_html}{links}'

    def clean_route_path(self):
        path = urlparse(self.path).path
        if path == "/index.html": return "/"
        return path.rstrip("/") or "/"

    def render_main_html(self, route_path):
        page_file = BASE / "index.html"
        try:
            page_html = page_file.read_text(encoding="utf-8")
        except OSError:
            return self.send_error(404, "Page not found")
        origin = self.public_origin()
        route_path = "/" if route_path == "/index.html" else route_path
        page = SEO_PAGES.get(route_path, SEO_PAGES["/"])
        canonical_path = route_path if route_path in SEO_PAGES else "/"
        canonical_url = html.escape(origin + canonical_path, quote=True)
        image_url = html.escape(origin + "/assets/og-image-v30.png", quote=True)
        title = html.escape(page["title"], quote=True)
        description = html.escape(page["description"], quote=True)
        robots = "index,follow,max-image-preview:large" if page.get("index") else "noindex,nofollow"
        graph = {
            "@context": "https://schema.org",
            "@graph": [
                {"@type": "WebSite", "@id": origin + "/#website", "url": origin + "/", "name": "WriteWiser", "alternateName": "WriteWiser IELTS", "inLanguage": "vi-VN", "description": SEO_PAGES["/"]["description"], "publisher": {"@id": origin + "/#organization"}},
                {"@type": "Organization", "@id": origin + "/#organization", "name": "WriteWiser", "url": origin + "/", "logo": {"@type": "ImageObject", "url": origin + "/assets/favicon-512.png", "width": 512, "height": 512}, "sameAs": ["https://www.facebook.com/people/WriteWiser/61595026757886/"]},
                {"@type": "WebPage", "@id": canonical_url + "#webpage", "url": origin + canonical_path, "name": page["title"], "description": page["description"], "inLanguage": "vi-VN", "isPartOf": {"@id": origin + "/#website"}},
            ]
        }
        if route_path == "/":
            graph["@graph"].append({"@type": "SoftwareApplication", "@id": origin + "/#app", "name": "WriteWiser", "url": origin + "/", "applicationCategory": "EducationalApplication", "operatingSystem": "Web", "inLanguage": "vi-VN", "description": SEO_PAGES["/"]["description"], "offers": {"@type": "Offer", "price": "0", "priceCurrency": "VND"}})
        jsonld = json.dumps(graph, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        page_html = page_html.replace("__WW_PUBLIC_URL__", canonical_url).replace("__WW_OG_IMAGE_URL__", image_url)
        page_html = page_html.replace("__WW_SEO_TITLE__", title).replace("__WW_SEO_DESCRIPTION__", description)
        page_html = page_html.replace("__WW_SEO_H1__", html.escape(page.get("heading", "Không gian học tập riêng tư.")))
        page_html = page_html.replace("__WW_SEO_JSONLD__", jsonld).replace("__WW_SEO_GATE_CONTENT__", self.render_seo_gate_content(page))
        page_html = page_html.replace('<meta name="robots" content="index,follow">', f'<meta name="robots" content="{robots}">')
        raw = page_html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("X-Content-Type-Options", "nosniff")
        if not page.get("index"):
            self.send_header("X-Robots-Tag", "noindex, nofollow")
        self.end_headers()
        self.wfile.write(raw)

    def list_directory(self, path):
        # Never expose directory listings (e.g. /assets/ or /resources/)
        self.send_error(404, "Directory listing is disabled")
        return None

    def do_GET(self):
        raw_path = urlparse(self.path).path
        path = posixpath.normpath(unquote(raw_path))
        if not path.startswith("/"):
            path = "/" + path
        if raw_path == "/":
            path = "/"
        # Serve the root favicon explicitly, without requiring a study-gate session.
        if path == "/favicon.ico":
            try:
                favicon_bytes = (BASE / "favicon.ico").read_bytes()
            except OSError:
                return self.send_error(404, "Favicon not found")
            self.send_response(200)
            self.send_header("Content-Type", "image/x-icon")
            self.send_header("Content-Length", str(len(favicon_bytes)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(favicon_bytes)
            return
        # Transparently support both og-image-v30.png and og-image-v30.jpg so social crawlers never 404
        if path in ("/assets/og-image-v30.png", "/assets/og-image-v30.jpg"):
            for candidate, mime in (("og-image-v30.png", "image/png"), ("og-image-v30.jpg", "image/jpeg")):
                f_path = BASE / "assets" / candidate
                if f_path.is_file():
                    raw = f_path.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", mime)
                    self.send_header("Content-Length", str(len(raw)))
                    self.send_header("Cache-Control", "public, max-age=86400")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    self.wfile.write(raw)
                    return
        # Do not let the static-file fallback expose backend/database/config files.
        filename = path.rsplit("/", 1)[-1]
        blocked_names = {
            "draftwise.sqlite3", "server.py", "ww_db.py", "ww_ai.py", "ww_importer.py", "ww_scoring.py",
            "providers.env.example", "render.yaml", "README.md", "requirements.txt",
            "migrate_sqlite_to_postgres.py", "run_writewiser.bat", ".gitignore", ".env",
        }
        blocked_exts = (".py", ".sqlite3", ".sqlite3-wal", ".sqlite3-shm", ".yaml", ".yml", ".bat", ".env")
        if (
            path.startswith("/resources")
            or path.startswith("/.")
            or filename.startswith(".")
            or filename in blocked_names
            or filename.lower().endswith(blocked_exts)
            or path.startswith("/draftwise.sqlite3")
        ):
            return self.send_error(404, "Not found")
        if path != "/" and path.endswith("/") and path[:-1] in SEO_PAGES:
            self.send_response(301)
            self.send_header("Location", path[:-1])
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            return
        if path == "/robots.txt":
            origin = self.public_origin()
            body = "User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /draftwise.sqlite3\n\nSitemap: " + origin + "/sitemap.xml\n"
            return self.send_text_response(200, "text/plain; charset=utf-8", body)
        if path == "/sitemap.xml":
            return self.send_text_response(200, "application/xml; charset=utf-8", self.build_sitemap())
        # Friendly paths are served by the same SPA shell but contain distinct,
        # server-rendered metadata and crawlable public summaries.
        if path in SEO_PAGES or path == "/index.html":
            return self.render_main_html(path)
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
                return self.send_json(200, {"ok": True, "service": "writewiser", "version": "47-modular-perf-cors-flashcard", "database": "postgres" if DATABASE_URL else "sqlite-local", "aiConfigured": any(os.environ.get(k) for k in ("GEMINI_API_KEY","GROQ_API_KEY","TOGETHER_API_KEY","OPENROUTER_API_KEY")) or any(os.environ.get(c.get("key_env", "")) for c in custom_openai_compatible_providers().values()), "providers": [p for p,k in (("Gemini","GEMINI_API_KEY"),("Groq","GROQ_API_KEY"),("Together AI","TOGETHER_API_KEY"),("OpenRouter","OPENROUTER_API_KEY")) if os.environ.get(k)] + [cfg["label"] for name,cfg in custom_openai_compatible_providers().items() if os.environ.get(cfg["key_env"])], "providerOrder": os.environ.get("AI_PROVIDER_ORDER","gemini,groq,together,openrouter"), "model": os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")})
            if path == "/api/scoring-status":
                prompt_path = BASE / "resources" / "ielts_writing_master_prompt.txt"
                try:
                    master_prompt = prompt_path.read_text(encoding="utf-8")
                    return self.send_json(200, {"masterPromptLoaded": bool(master_prompt.strip()), "promptChars": len(master_prompt), "promptSha256": hashlib.sha256(master_prompt.encode("utf-8")).hexdigest(), "promptFile": "resources/ielts_writing_master_prompt.txt", "appliedTo": "/api/writing-feedback", "criteriaBands": "integer 0-9", "overallBand": "nearest half-band", "version": "39"})
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
            if path == "/api/feedback-corrections/mine":
                user=self.session_user()
                if not user:return self.send_json(401,{"error":"Bạn cần đăng nhập."})
                with db() as c:
                    rows=c.execute("SELECT id,payload_json,created_at FROM feedback_corrections WHERE user_id=? ORDER BY created_at ASC",(user["id"],)).fetchall()
                # Return only correction data needed to restore this account's score tags.
                # Never expose another user's essay or corrections.
                latest={}
                for row in rows:
                    try: payload=json.loads(row["payload_json"])
                    except Exception: continue
                    record_id=payload.get("recordId") if isinstance(payload,dict) else None
                    corrected=payload.get("corrected_feedback") if isinstance(payload,dict) else None
                    if record_id is None or not isinstance(corrected,dict): continue
                    latest[str(record_id)]={"recordId":str(record_id),"correctedFeedback":corrected,"createdAt":int(row["created_at"]),"correctionId":row["id"]}
                return self.send_json(200,{"items":list(latest.values())})
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
            if path == "/api/notes":
                user = self.session_user()
                if not user:
                    return self.send_json(401, {"error": "Đăng nhập để tải sổ tay ghi chú đã đồng bộ."})
                with db() as c:
                    rows = c.execute("SELECT note_json FROM user_notes WHERE user_id=? ORDER BY created_at DESC", (user["id"],)).fetchall()
                notes = []
                for row in rows:
                    try:
                        note = json.loads(row["note_json"])
                        if isinstance(note, dict): notes.append(note)
                    except Exception:
                        continue
                return self.send_json(200, {"notes": notes, "count": len(notes), "storage": "account-database"})
            if path == "/api/history":
                user = self.session_user()
                if not user:
                    return self.send_json(401, {"error": "Bạn cần đăng nhập để tải lịch sử đã đồng bộ."})
                with db() as c:
                    # One-time/backward-compatible migration from the old user_state JSON blob.
                    legacy = c.execute("SELECT state_json FROM user_state WHERE user_id=?", (user["id"],)).fetchone()
                    if legacy and legacy["state_json"]:
                        try:
                            old_state = json.loads(legacy["state_json"])
                            if isinstance(old_state, dict) and "history" in old_state:
                                upsert_user_history(c, user["id"], old_state.pop("history", []))
                                # Remove the migrated copy so an intentional history deletion is not undone later.
                                c.execute("UPDATE user_state SET state_json=?, updated_at=? WHERE user_id=?",
                                          (json.dumps(old_state, ensure_ascii=False), int(time.time()), user["id"]))
                        except Exception:
                            pass
                    records = read_user_history(c, user["id"])
                return self.send_json(200, {"records": records, "count": len(records), "storage": "account-database"})
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
            if path == "/api/image-proxy":
                from urllib.parse import parse_qs
                query = parse_qs(urlparse(self.path).query)
                img_url = (query.get("url") or [""])[0].strip()
                try:
                    raw, mime = fetch_remote_or_local_image(img_url, base_dir=BASE)
                    if not raw or not mime:
                        return self.send_json(400, {"error": "URL ảnh không hợp lệ hoặc không nằm trong danh sách cho phép."})
                    self.send_response(200)
                    self.send_header("Content-Type", mime)
                    self.send_header("Content-Length", str(len(raw)))
                    self.send_header("Cache-Control", "public, max-age=86400")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                except Exception as exc:
                    return self.send_json(502, {"error": "Không tải được ảnh từ máy chủ nguồn: " + str(exc)[:160]})
            if path == "/api/dashboard":
                user = self.session_user()
                if not user:
                    return self.send_json(401, {"error": "Đăng nhập để xem Dashboard và bảng xếp hạng."})
                with db() as c:
                    own_history = read_user_history(c, user["id"])
                    leaderboard = compute_cached_leaderboard(c)
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
        # Calibration remains a private tool and is served only to users with a valid gate session.
        if path == "/calibration.html":
            page_file = BASE / "calibration.html"
            try:
                page_html = page_file.read_text(encoding="utf-8")
            except OSError:
                return self.send_error(404, "Page not found")
            origin = self.public_origin()
            public_url = html.escape(origin + "/calibration.html", quote=True)
            image_url = html.escape(origin + "/assets/og-image-v30.png", quote=True)
            page_html = page_html.replace("__WW_PUBLIC_URL__", public_url).replace("__WW_OG_IMAGE_URL__", image_url)
            raw = page_html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("X-Robots-Tag", "noindex, nofollow")
            self.send_header("Cache-Control", "no-store")
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
        elif path in ("/api/translate", "/api/feedback", "/api/writing-feedback", "/api/brainstorm-review", "/api/notes/generate"):
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
        if path == "/api/notes/generate":
            selected_text = str(data.get("selectedText", "")).strip()
            if not selected_text:
                return self.send_json(400, {"error": "Hãy nhập hoặc chọn một từ, cụm từ, vế câu hoặc câu trước khi tạo ghi chú."})
            if len(selected_text) > 1800:
                return self.send_json(413, {"error": "Đoạn chọn quá dài. Hãy chọn một từ, cụm từ, vế câu hoặc tối đa một vài câu."})
            context = str(data.get("context", "")).strip()[:1200]
            lesson_title = str(data.get("lessonTitle", "")).strip()[:200]
            task = str(data.get("task", "")).strip()[:20]
            mode = str(data.get("mode", "")).strip()[:30]
            system_instruction = """Bạn là gia sư tiếng Anh học thuật cho người Việt đang học IELTS Writing. Hãy biến nội dung người học cung cấp (do tự nhập hoặc chọn từ bài viết) thành một ghi chú có thể dùng để học lại. Chỉ trả về MỘT JSON object hợp lệ, không Markdown, đúng schema: {"expression":string,"category":string,"note":string,"meaning_vi":string,"usage":string,"example_en":string,"example_vi":string}.

YÊU CẦU: (1) Đoạn được chọn có thể là từ đơn, cụm từ, mệnh đề, vế câu hoặc cả câu; xác định đúng loại và chọn độ sâu giải thích phù hợp. (2) Nếu đoạn chọn bằng tiếng Anh, giữ nguyên cách viết trong trường expression trừ khi cần nêu dạng chuẩn phổ biến hơn; nếu là tiếng Việt, đưa ra cách diễn đạt tiếng Anh tự nhiên, phù hợp ngữ cảnh IELTS ở trường expression. (3) meaning_vi phải giải thích nghĩa tiếng Việt sát ngữ cảnh, không chỉ dịch máy móc. (4) note là điểm ghi nhớ quan trọng, sắc thái nghĩa hoặc lý do cách diễn đạt này hữu ích; tránh lặp lại meaning_vi. (5) usage nêu cấu trúc, ngữ pháp, collocation, giới từ đi kèm và mức độ trang trọng khi phù hợp; không bịa quy tắc không chắc chắn. Với cả câu, giải thích cấu trúc đáng học. (6) example_en là một ví dụ tiếng Anh mới, tự nhiên, có thể áp dụng trong IELTS Writing, không sao chép nguyên đoạn được chọn; example_vi là bản dịch tiếng Việt chính xác của ví dụ. (7) Viết hoàn toàn bằng tiếng Việt cho note, meaning_vi và usage. Trường category phải đúng một trong các giá trị: Từ, Cụm từ, Mệnh đề / vế câu, Câu, Cách diễn đạt. Trả lời hữu ích nhưng súc tích. Không bịa ngữ cảnh; nếu ngữ cảnh chưa rõ, ghi rõ nghĩa phổ biến nhất."""
            prompt = ("Hãy tạo ghi chú cho nội dung người học muốn học dưới đây. Nội dung có thể do người học tự nhập hoặc chọn từ bài viết.\n\nNỘI DUNG CẦN GHI CHÚ:\n" + selected_text +
                      "\n\nNGỮ CẢNH XUNG QUANH (có thể trống):\n" + (context or "Không có ngữ cảnh bổ sung") +
                      "\n\nBÀI LUYỆN: " + (lesson_title or "Không xác định") +
                      "\nTASK: " + (task or "Không xác định") +
                      "\nCHẾ ĐỘ: " + (mode or "Không xác định") +
                      "\n\nTrả về đủ bảy trường JSON theo schema.")
            try:
                generated, model = ai_generate(system_instruction, prompt, max_output_tokens=2200)
                if not isinstance(generated, dict):
                    return self.send_json(502, {"error": "AI trả về ghi chú không đúng định dạng. Hãy thử lại."})
                def note_field(key, limit):
                    value = generated.get(key, "")
                    if not isinstance(value, str): value = str(value) if value is not None else ""
                    return value.strip()[:limit]
                expression = note_field("expression", 500) or selected_text[:500]
                raw_category = note_field("category", 40).strip().lower()
                category_aliases = {
                    "từ": "Từ", "word": "Từ", "vocabulary": "Từ",
                    "cụm từ": "Cụm từ", "phrase": "Cụm từ", "collocation": "Cụm từ", "idiom": "Cụm từ",
                    "mệnh đề / vế câu": "Mệnh đề / vế câu", "clause": "Mệnh đề / vế câu", "phrase/clause": "Mệnh đề / vế câu",
                    "câu": "Câu", "sentence": "Câu", "expression": "Cách diễn đạt", "cách diễn đạt": "Cách diễn đạt"
                }
                category = category_aliases.get(raw_category, "Cách diễn đạt")
                note = {
                    "id": secrets.token_urlsafe(16),
                    "expression": expression,
                    "category": category,
                    "note": note_field("note", 1800),
                    "meaning_vi": note_field("meaning_vi", 1800),
                    "usage": note_field("usage", 2400),
                    "example_en": note_field("example_en", 1200),
                    "example_vi": note_field("example_vi", 1200),
                    "selected_text": selected_text[:1800],
                    "context": context[:1200],
                    "lessonTitle": lesson_title,
                    "task": task,
                    "createdAt": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                    "model": str(model)[:120]
                }
                if not note["note"] or not note["meaning_vi"] or not note["usage"] or not note["example_en"] or not note["example_vi"]:
                    return self.send_json(502, {"error": "AI tạo ghi chú thiếu trường bắt buộc. Hãy thử tạo lại."})
                user = self.session_user()
                if user:
                    now = int(time.time())
                    with db() as c:
                        c.execute("INSERT INTO user_notes(user_id,note_id,note_json,created_at) VALUES(?,?,?,?)", (user["id"], note["id"], json.dumps(note, ensure_ascii=False), now))
                return self.send_json(200, {"note": note, "saved": bool(user), "storage": "account-database" if user else "browser-local", "model": model})
            except RuntimeError as e:
                message = str(e)
                code = 429 if "quota" in message.lower() or "429" in message else 502
                return self.send_json(code, {"error": "Không tạo được ghi chú AI. " + message[:350]})
            except Exception:
                return self.send_json(500, {"error": "AI đã xử lý nhưng máy chủ không lưu được ghi chú. Hãy thử lại hoặc kiểm tra kết nối database."})
        if path == "/api/history":
            user = self.session_user()
            if not user:
                return self.send_json(401, {"error": "Bạn cần đăng nhập để đồng bộ lịch sử."})
            records = data.get("records")
            if not isinstance(records, list):
                single = data.get("record")
                records = [single] if isinstance(single, dict) else None
            if records is None:
                return self.send_json(400, {"error": "Thiếu danh sách bản ghi lịch sử hợp lệ."})
            if len(records) > 5000:
                return self.send_json(413, {"error": "Số bản ghi gửi lên quá lớn."})
            with db() as c:
                saved = upsert_user_history(c, user["id"], records)
            return self.send_json(200, {"ok": True, "saved": saved})
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
                out = {}
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        if k in ("corrections", "filled", "reviewSummary", "reviewData", "fieldReviews", "reviewedAt", "schemaVersion", "expansion"):
                            continue
                        field_path = f"{prefix}.{k}" if prefix else k
                        if isinstance(v, dict):
                            out.update(flat_map(v, field_path))
                        elif isinstance(v, str):
                            out[field_path] = v[:2500]
                return out

            all_fields = flat_map(mindmap)
            # Only analyse the visible alternative (Example OR Explain 2). Never assess a hidden branch.
            for body_name in ("body1", "body2"):
                body_value = mindmap.get(body_name, {}) if isinstance(mindmap, dict) else {}
                if not isinstance(body_value, dict):
                    continue
                for idea_name in ("idea1", "idea2"):
                    idea_value = body_value.get(idea_name, {})
                    if isinstance(idea_value, dict):
                        prefix = f"{body_name}.{idea_name}."
                        expansion = idea_value.get("expansion", "example")
                        if expansion == "example":
                            all_fields.pop(prefix + "explain2", None)
                            all_fields.pop(prefix + "result", None)
                        elif expansion == "explain2":
                            all_fields.pop(prefix + "example", None)
                            all_fields.pop(prefix + "result", None)
                        elif expansion == "result":
                            all_fields.pop(prefix + "example", None)
                            all_fields.pop(prefix + "explain2", None)
                        else:
                            all_fields.pop(prefix + "explain2", None)
                            all_fields.pop(prefix + "result", None)

            # Content and Explain 1 are the required brainstorm fields and can still receive suggestions.
            # An empty optional Example/Explain 2 is omitted entirely: it is not reviewed or auto-filled.
            required_suffixes = (".content", ".explain1")
            fields = {
                key: value for key, value in all_fields.items()
                if value.strip() or key.endswith(required_suffixes)
            }
            nonempty_fields = {key: value for key, value in fields.items() if value.strip()}
            required_blank_paths = [key for key, value in fields.items() if not value.strip() and key.endswith(required_suffixes)]
            if len(fields) > 30:
                return self.send_json(400, {"error": "Mindmap có quá nhiều trường."})

            criterion = "Task Achievement (TA)" if task == "Task 1" else "Task Response (TR)"
            system = f"""Bạn là chuyên gia phân tích đề IELTS Writing và huấn luyện phát triển lập luận bằng mindmap. Phản hồi hoàn toàn bằng tiếng Việt, cụ thể, nghiêm túc và bám sát từng chữ trong đề.

MỤC TIÊU ƯU TIÊN CAO NHẤT — TASK RESPONSE / TASK ACHIEVEMENT:
1) Đọc kỹ toàn bộ câu hỏi, phân loại dạng đề, tách đề thành TỪNG yêu cầu nhỏ bắt buộc phải trả lời. Với Task 2, kiểm tra mọi phần như: nêu quan điểm đến mức nào, thảo luận cả hai phía, nguyên nhân, hệ quả, giải pháp, hai câu hỏi riêng, lợi/hại, so sánh, hoặc phạm vi đối tượng/thời gian. Không mặc định mọi đề chỉ cần “ý liên quan”; phải kiểm tra ý đó có thực sự trả lời câu hỏi được hỏi hay chỉ nói chung chung cùng chủ đề.
2) Đối chiếu từng yêu cầu với cả 4 ý tưởng và các trường phát triển hiện có. Ghi rõ yêu cầu nào đã đáp ứng, đáp ứng một phần, hoặc bỏ sót; dẫn đường dẫn trường (Body/Idea/Explain/Example/Result) và nêu chính xác phần lập luận đang thiếu.
3) Với MỖI Idea chính có nội dung, đánh giá: (a) có sát đề không; (b) có trực tiếp hỗ trợ câu trả lời/quan điểm không; (c) nếu viết thành đoạn thân bài thì chuỗi lập luận đã đủ sâu chưa: luận điểm → lý do/cơ chế (Explain) → minh họa cụ thể (Example khi cần) → hệ quả/kết quả (Result khi hữu ích). Không bắt buộc mọi ý đều phải có đủ cả ba Explain/Example/Result; chọn phần thiếu có giá trị nhất cho ý đó. Tránh gợi ý chung chung kiểu “cần giải thích thêm”. Hãy viết gợi ý nội dung cụ thể có thể đưa thẳng vào mindmap, không bịa số liệu, nghiên cứu hoặc sự kiện như dữ kiện thật.
4) Ước lượng band TIỀM NĂNG riêng của {criterion} (TA cho Task 1, TR cho Task 2) NẾU bài viết được triển khai dựa đúng các ý brainstorm hiện có và được diễn đạt thành đoạn văn hợp lý, nhưng không được tự thêm luận điểm quan trọng chưa có trong mindmap. Ước lượng theo dải band nửa điểm, cho low_band, high_band và likely_band, đồng thời nêu bằng chứng và độ tin cậy. Đây chỉ là ước lượng sơ bộ khả năng đáp ứng TA/TR của kế hoạch ý tưởng, KHÔNG phải điểm bài viết thực tế, không phải Overall Writing Band và không phải điểm chính thức. Không đánh giá CC/LR/GRA.
5) Nếu là Task 1, nhắc rõ giới hạn nếu mindmap không cung cấp đủ số liệu/biểu đồ/hình ảnh để kiểm tra việc chọn đặc điểm chính; không được bịa số liệu. Có thể đánh giá độ bao phủ dựa trên đề và phần đã nhập, nhưng phải hạ độ tin cậy khi thiếu dữ liệu trực quan.

PHÂN LOẠI ĐỘ SÁT ĐỀ VÀ ĐỘ SÂU:
- Với mỗi yêu cầu đề: status = covered | partly_covered | missing. `supporting_paths` chỉ dẫn trường thực tế có liên quan. `evidence` tóm tắt hoặc trích đúng nội dung đã nhập; không bịa dẫn chứng. `gap` nêu phần còn thiếu; `revision` đưa hành động sửa rõ ràng.
- Với mỗi Idea có nội dung: relevance = on_topic | partly_on_topic | off_topic; answer_status = answers | partly_answers | does_not_answer; depth = sufficient | needs_development | very_thin. “Cùng chủ đề” không đồng nghĩa “trả lời đúng câu hỏi”. Nếu lạc đề, giải thích tại sao không trả lời đúng yêu cầu nào và đề xuất hướng thay thế bám vào từ khóa/lệnh hỏi của đề.
- Gợi ý phát triển mỗi ý tối đa 1–2 ưu tiên, với `recommendation_type` là explain | example | result | explain2 | revise_idea. `target_path` phải đúng một trường trong cùng Idea: content, explain1, example, explain2 hoặc result. Nếu ý lạc đề thì hướng dẫn sửa Idea, target_path có thể là trường content; không tự áp dụng thay người dùng. `suggested_text` phải là một câu/ý cụ thể, có thể đưa vào mindmap ngay. `reason` nói rõ nó vá lỗ hổng lập luận hoặc giúp trả lời phần nào của đề.

KHÔNG CHẤM NGỮ PHÁP:
Không nhận xét tense, articles, subject-verb agreement, word order, câu thiếu chủ-vị, câu chưa hoàn chỉnh, fragment, dấu câu, spelling hoặc độ phức tạp câu. Mindmap được phép dùng từ khóa, mũi tên, tiếng Việt xen tiếng Anh và câu chưa hoàn chỉnh. Không đề xuất sửa grammar. Nếu có nhận xét từ vựng, chỉ xét từ/cụm tiếng Anh có phù hợp nghĩa, sắc thái, collocation và ngữ cảnh không.

ĐÁNH DẤU LỖI TỪ VỰNG / LẠC ĐỀ:
- Mỗi issue phải có `segment` là một đoạn NGUYÊN VĂN, liên tục, chép chính xác và có thật trong giá trị trường gốc; chỉ chọn từ/cụm ngắn nhất thực sự có vấn đề, không tô đỏ toàn bộ trường nếu chỉ sai một cụm.
- `replacement` sửa tối thiểu segment đó. Với ý lạc đề, khoanh cụm ngắn nhất thể hiện ý sai và đưa cách thay thế bám đề khi có thể.
- Chỉ flag vấn đề về lựa chọn từ/collocation/nghĩa hoặc ý không đáp ứng đề. Không gọi grammar/spelling là issue.
- Trường rỗng chỉ được gợi ý nếu là trường bắt buộc content hoặc explain1; trường tùy chọn rỗng đã bị loại khỏi dữ liệu, tuyệt đối không đánh giá hay tự điền các trường đó.

JSON BẮT BUỘC theo schema dưới đây. Mọi nhận xét phải gắn với câu hỏi và dữ liệu cụ thể, tránh lời khen/chê chung chung:
{{
 "overall_summary":"Kết luận 3–6 câu: đề yêu cầu gì, mức độ đáp ứng, lỗ hổng quan trọng nhất, ước lượng band TA/TR và cảnh báo đó là ước lượng mindmap.",
 "question_analysis":{{"question_type":"...","core_demand":"...","coverage_conclusion":"...","requirements":[{{"requirement":"một yêu cầu riêng biệt trong đề","status":"covered|partly_covered|missing","supporting_paths":["body1.idea1.content"],"evidence":"dẫn chứng/paraphrase trung thành từ mindmap hoặc để trống nếu chưa có","gap":"phần thiếu hoặc lý do chưa đủ","revision":"hành động cụ thể để đáp ứng yêu cầu"}}]}},
 "criterion_estimate":{{"criterion":"TA|TR","low_band":5.5,"high_band":6.0,"likely_band":6.0,"confidence":"low|medium|high","rationale":"lý do gắn với phạm vi đề, độ bao phủ, liên quan, phát triển ý","limitations":"giới hạn do đây mới là brainstorm, không phải bài viết hoàn chỉnh"}},
 "idea_development":[{{"path":"body1.idea1.content","idea_summary":"tóm tắt trung thành Idea của người dùng","relevance":"on_topic|partly_on_topic|off_topic","answer_status":"answers|partly_answers|does_not_answer","depth":"sufficient|needs_development|very_thin","what_works":"phần lập luận có ích hiện tại","gap":"lỗ hổng cụ thể và yêu cầu đề chưa được xử lý","recommendation_type":"explain|example|result|explain2|revise_idea","target_path":"body1.idea1.example","suggested_text":"gợi ý nội dung cụ thể để thêm/sửa","reason":"vì sao phần này giúp ý trả lời đề tốt hơn"}}],
 "field_reviews":{{"body1.idea1.content":{{"status":"correct|needs_revision","vocabulary_assessment":"appropriate|mixed|inappropriate|not_assessed","idea_relevance":"relevant|partly_relevant|irrelevant|not_assessed","note":"nhận xét ngắn bằng tiếng Việt, ưu tiên mức độ trả lời đề","issues":[{{"segment":"đoạn nguyên văn từ trường","replacement":"cụm thay thế","dimension":"vocabulary|idea","reason":"lý do gắn với đề"}}]}}}},
 "fill_required_blanks":{{"body1.idea1.content":"gợi ý ngắn gọn cho ô bắt buộc còn trống","body1.idea1.explain1":"gợi ý giải thích ngắn gọn"}},
 "summary_categories":{{
   "valid_vocabulary":[{{"path":"body1.idea1.explain1","segment":"cụm nguyên văn","reason":"vì sao phù hợp"}}],
   "vocabulary_to_fix":[{{"path":"body1.idea1.content","segment":"cụm nguyên văn","replacement":"cụm thay thế","reason":"lý do"}}],
   "relevant_ideas":[{{"path":"body1.idea1.content","idea":"tóm tắt ngắn ý đã viết","reason":"điểm phù hợp với yêu cầu cụ thể của đề"}}],
   "irrelevant_ideas":[{{"path":"body2.idea2.content","idea":"tóm tắt ngắn ý chưa phù hợp","reason":"yêu cầu nào bị bỏ lỡ/ý này không trả lời đề ra sao","suggestion":"hướng sửa bám đề"}}],
   "changes_needed":[{{"path":"body1.idea1.content","segment":"cụm nguyên văn cần sửa","replacement":"cụm đề xuất","reason":"lý do"}}]
 }}
}}
NGUYÊN TẮC: question_analysis.requirements phải bao quát từng phần mệnh lệnh/yêu cầu của đề, không gộp mất câu hỏi thứ hai. idea_development phải xem xét từng Idea chính không rỗng. Chỉ trích dẫn nội dung thực sự có trong mindmap. Không thêm band score ngoài criterion_estimate. Tất cả các trường là JSON hợp lệ, không có Markdown bên ngoài."""
            prompt_data = {
                "task": task,
                "question": question,
                "question_type": str(data.get("type", ""))[:100],
                "topic": str(data.get("topic", ""))[:100],
                "fields": fields,
                "required_blank_paths": required_blank_paths,
                "focus": ["tách đề thành từng yêu cầu bắt buộc", "đối chiếu từng yêu cầu với brainstorm", "ước lượng sơ bộ band TA/TR", "độ sâu từng Idea và gợi ý Explain/Example/Result", "từ vựng/collocation phù hợp ngữ cảnh"],
                "excluded": ["ngữ pháp", "spelling", "độ hoàn chỉnh câu", "dấu câu", "Overall Writing Band"]
            }
            try:
                review, model = ai_generate(system, "Rà soát mindmap theo đúng phạm vi đã nêu, trả JSON theo schema. Dữ liệu:\n" + json.dumps(prompt_data, ensure_ascii=False), max_output_tokens=8000)
                if not isinstance(review, dict) or not isinstance(review.get("field_reviews", {}), dict):
                    return self.send_json(502, {"error": "AI trả về cấu trúc Brainstorm không hợp lệ. Hãy thử lại."})

                # Strictly validate paths and exact source segments so only small, real excerpts get highlighted.
                # Reject AI comments that drift back into grammar/spelling feedback despite the prompt.
                def mentions_grammar_feedback(text):
                    return bool(re.search(r"\b(grammar|grammatical|spelling|punctuation|tense|article|articles|subject.?verb|word order|fragment)\b|ngữ pháp|chính tả|dấu câu|mạo từ|chia động từ|trật tự từ|câu thiếu chủ", str(text or ""), re.IGNORECASE))
                def safe_scope_comment(text, fallback):
                    text = str(text or "")[:600]
                    return fallback if mentions_grammar_feedback(text) else text

                clean_field_reviews = {}
                for field_path, item in review.get("field_reviews", {}).items():
                    original = nonempty_fields.get(field_path)
                    if not original or not isinstance(item, dict):
                        continue
                    clean_issues = []
                    for issue in item.get("issues", []) if isinstance(item.get("issues", []), list) else []:
                        if not isinstance(issue, dict):
                            continue
                        segment = str(issue.get("segment", ""))[:500]
                        replacement = str(issue.get("replacement", ""))[:500]
                        dimension = str(issue.get("dimension", "vocabulary"))
                        reason_text = str(issue.get("reason", ""))[:400]
                        if not segment or segment not in original or not replacement.strip() or mentions_grammar_feedback(reason_text):
                            continue
                        if dimension not in ("vocabulary", "idea"):
                            dimension = "vocabulary"
                        clean_issues.append({
                            "segment": segment,
                            "replacement": replacement,
                            "dimension": dimension,
                            "reason": reason_text
                        })
                    status = "needs_revision" if clean_issues else "correct"
                    vocabulary_assessment = str(item.get("vocabulary_assessment", "not_assessed"))
                    if vocabulary_assessment not in ("appropriate", "mixed", "inappropriate", "not_assessed"):
                        vocabulary_assessment = "not_assessed"
                    idea_relevance = str(item.get("idea_relevance", "not_assessed"))
                    if idea_relevance not in ("relevant", "partly_relevant", "irrelevant", "not_assessed"):
                        idea_relevance = "not_assessed"
                    clean_field_reviews[field_path] = {
                        "status": status,
                        "vocabulary_assessment": vocabulary_assessment,
                        "idea_relevance": idea_relevance,
                        "note": safe_scope_comment(item.get("note", ""), "Không phát hiện vấn đề cần sửa ngoài phạm vi từ vựng và độ sát đề."),
                        "issues": clean_issues
                    }

                # Return an explicit status for every populated, visible field. Never assess optional blanks.
                for field_path in nonempty_fields:
                    clean_field_reviews.setdefault(field_path, {
                        "status": "correct",
                        "vocabulary_assessment": "not_assessed",
                        "idea_relevance": "not_assessed",
                        "note": "Không có điểm cần sửa được ghi nhận về lựa chọn từ/cụm từ hoặc độ sát đề. Ngữ pháp không được chấm.",
                        "issues": []
                    })

                fill_source = review.get("fill_required_blanks", {})
                clean_fill = {}
                if isinstance(fill_source, dict):
                    for field_path, value in fill_source.items():
                        if field_path in required_blank_paths and isinstance(value, str) and value.strip():
                            clean_fill[field_path] = value.strip()[:1200]

                def clean_summary_items(key, requires_text_field=False):
                    source_items = review.get("summary_categories", {}).get(key, []) if isinstance(review.get("summary_categories", {}), dict) else []
                    out = []
                    if not isinstance(source_items, list):
                        return out
                    for item in source_items[:30]:
                        if not isinstance(item, dict):
                            continue
                        field_path = str(item.get("path", ""))
                        original = nonempty_fields.get(field_path)
                        if not original:
                            continue
                        cleaned = {"path": field_path}
                        if key in ("valid_vocabulary", "vocabulary_to_fix", "changes_needed"):
                            segment = str(item.get("segment", ""))[:500]
                            if not segment or segment not in original:
                                continue
                            cleaned["segment"] = segment
                        elif key in ("relevant_ideas", "irrelevant_ideas"):
                            if not field_path.endswith(".content"):
                                continue
                            cleaned["idea"] = safe_scope_comment(str(item.get("idea", ""))[:500], original[:500]) or original[:500]
                        if key in ("vocabulary_to_fix", "changes_needed"):
                            replacement = str(item.get("replacement", ""))[:500]
                            if not replacement.strip():
                                continue
                            cleaned["replacement"] = replacement
                        if key == "irrelevant_ideas":
                            cleaned["suggestion"] = safe_scope_comment(item.get("suggestion", ""), "Điều chỉnh ý theo từ khóa và yêu cầu trọng tâm của đề bài.")
                        cleaned["reason"] = safe_scope_comment(item.get("reason", ""), "Nhận xét chỉ xét độ phù hợp của từ/cụm từ hoặc ý tưởng với đề bài.")
                        out.append(cleaned)
                    return out

                category_keys = ("valid_vocabulary", "vocabulary_to_fix", "relevant_ideas", "irrelevant_ideas", "changes_needed")
                summary_categories = {key: clean_summary_items(key) for key in category_keys}
                # Derive missing summary entries from validated field-level results, without inventing source text.
                for field_path, item in clean_field_reviews.items():
                    original = nonempty_fields.get(field_path, "")
                    for issue in item["issues"]:
                        if issue["dimension"] == "vocabulary" and not any(x.get("path") == field_path and x.get("segment") == issue["segment"] for x in summary_categories["vocabulary_to_fix"]):
                            summary_categories["vocabulary_to_fix"].append({"path": field_path, "segment": issue["segment"], "replacement": issue["replacement"], "reason": issue["reason"]})
                        if not any(x.get("path") == field_path and x.get("segment") == issue["segment"] for x in summary_categories["changes_needed"]):
                            summary_categories["changes_needed"].append({"path": field_path, **issue})
                    if field_path.endswith(".content") and item.get("idea_relevance") in ("relevant", "partly_relevant") and not any(x.get("path") == field_path for x in summary_categories["relevant_ideas"]):
                        summary_categories["relevant_ideas"].append({"path": field_path, "idea": original[:500], "reason": item.get("note", "Ý tưởng có liên quan đến đề.")})
                    if field_path.endswith(".content") and item.get("idea_relevance") == "irrelevant" and not any(x.get("path") == field_path for x in summary_categories["irrelevant_ideas"]):
                        summary_categories["irrelevant_ideas"].append({"path": field_path, "idea": original[:500], "reason": item.get("note", "Ý tưởng cần điều chỉnh để sát đề hơn."), "suggestion": "Quay lại từ khóa và yêu cầu trọng tâm trong đề bài."})
                # Normalize the new task-coverage, projected TA/TR band, and idea-depth assessment.
                valid_brain_paths = {
                    f"{body}.{idea}.{field}"
                    for body in ("body1", "body2")
                    for idea in ("idea1", "idea2")
                    for field in ("content", "explain1", "example", "explain2", "result")
                }
                def safe_text(value, limit=900, fallback=""):
                    return safe_scope_comment(str(value or "").strip()[:limit], fallback)

                qa_src = review.get("question_analysis", {})
                if not isinstance(qa_src, dict): qa_src = {}
                clean_requirements = []
                req_src = qa_src.get("requirements", [])
                if isinstance(req_src, list):
                    for req in req_src[:12]:
                        if not isinstance(req, dict): continue
                        status_value = str(req.get("status", "missing"))
                        if status_value not in ("covered", "partly_covered", "missing"): status_value = "missing"
                        paths = req.get("supporting_paths", [])
                        if not isinstance(paths, list): paths = []
                        clean_paths = [str(p) for p in paths if str(p) in nonempty_fields][:8]
                        evidence = safe_text(req.get("evidence", ""), 700)
                        # Evidence is presented as a summary, not a quotation. If no populated field supports it, suppress it.
                        if evidence and not clean_paths:
                            evidence = ""
                        clean_requirements.append({
                            "requirement": safe_text(req.get("requirement", ""), 450, "Một yêu cầu của đề"),
                            "status": status_value,
                            "supporting_paths": clean_paths,
                            "evidence": evidence,
                            "gap": safe_text(req.get("gap", ""), 700),
                            "revision": safe_text(req.get("revision", ""), 700)
                        })
                question_analysis = {
                    "question_type": safe_text(qa_src.get("question_type", ""), 250),
                    "core_demand": safe_text(qa_src.get("core_demand", ""), 900),
                    "coverage_conclusion": safe_text(qa_src.get("coverage_conclusion", ""), 900),
                    "requirements": clean_requirements
                }

                band_src = review.get("criterion_estimate", {})
                if not isinstance(band_src, dict): band_src = {}
                def clean_band(value):
                    try: num = float(value)
                    except (TypeError, ValueError): return None
                    if not (0 <= num <= 9): return None
                    return round(num * 2) / 2
                low_band = clean_band(band_src.get("low_band"))
                high_band = clean_band(band_src.get("high_band"))
                likely_band = clean_band(band_src.get("likely_band"))
                if low_band is not None and high_band is not None and low_band > high_band:
                    low_band, high_band = high_band, low_band
                if low_band is not None and high_band is not None:
                    likely_band = likely_band if likely_band is not None else round(((low_band + high_band) / 2) * 2) / 2
                    likely_band = min(high_band, max(low_band, likely_band))
                else:
                    low_band = high_band = likely_band = None
                confidence = str(band_src.get("confidence", "low"))
                if confidence not in ("low", "medium", "high"): confidence = "low"
                criterion_estimate = {
                    "criterion": "TA" if task == "Task 1" else "TR",
                    "low_band": low_band,
                    "high_band": high_band,
                    "likely_band": likely_band,
                    "confidence": confidence,
                    "rationale": safe_text(band_src.get("rationale", ""), 1300),
                    "limitations": safe_text(band_src.get("limitations", ""), 900, "Đây chỉ là ước lượng từ mindmap, không phải điểm bài viết hoàn chỉnh.")
                }

                development_src = review.get("idea_development", [])
                clean_development = []
                if isinstance(development_src, list):
                    for item in development_src[:8]:
                        if not isinstance(item, dict): continue
                        field_path = str(item.get("path", ""))
                        if field_path not in valid_brain_paths or not field_path.endswith(".content"):
                            continue
                        original_idea = nonempty_fields.get(field_path, "")
                        if not original_idea: continue
                        relevance = str(item.get("relevance", "partly_on_topic"))
                        if relevance not in ("on_topic", "partly_on_topic", "off_topic"): relevance = "partly_on_topic"
                        answer_status = str(item.get("answer_status", "partly_answers"))
                        if answer_status not in ("answers", "partly_answers", "does_not_answer"): answer_status = "partly_answers"
                        depth = str(item.get("depth", "needs_development"))
                        if depth not in ("sufficient", "needs_development", "very_thin"): depth = "needs_development"
                        recommendation = str(item.get("recommendation_type", "explain"))
                        if recommendation not in ("explain", "example", "result", "explain2", "revise_idea"): recommendation = "explain"
                        target_path = str(item.get("target_path", ""))
                        allowed_for_idea = {field_path, field_path.rsplit(".", 1)[0] + ".content", field_path.rsplit(".", 1)[0] + ".explain1", field_path.rsplit(".", 1)[0] + ".example", field_path.rsplit(".", 1)[0] + ".explain2", field_path.rsplit(".", 1)[0] + ".result"}
                        target_slot = field_path.rsplit(".", 1)[0]
                        type_targets = {
                            "explain": {target_slot + ".explain1", target_slot + ".explain2"},
                            "explain2": {target_slot + ".explain2"},
                            "example": {target_slot + ".example"},
                            "result": {target_slot + ".result"},
                            "revise_idea": {target_slot + ".content"}
                        }
                        if target_path not in allowed_for_idea or target_path not in type_targets[recommendation]:
                            target_path = target_slot + ({"explain": ".explain1", "explain2": ".explain2", "example": ".example", "result": ".result", "revise_idea": ".content"}[recommendation])
                        clean_development.append({
                            "path": field_path,
                            "idea_summary": safe_text(item.get("idea_summary", ""), 500, original_idea[:500]),
                            "relevance": relevance,
                            "answer_status": answer_status,
                            "depth": depth,
                            "what_works": safe_text(item.get("what_works", ""), 700),
                            "gap": safe_text(item.get("gap", ""), 900),
                            "recommendation_type": recommendation,
                            "target_path": target_path,
                            "suggested_text": safe_text(item.get("suggested_text", ""), 1000),
                            "reason": safe_text(item.get("reason", ""), 700)
                        })

                overall_summary = safe_scope_comment(str(review.get("overall_summary", ""))[:2200].strip(), "Tổng kết dựa trên độ bao phủ yêu cầu đề, độ sâu các ý và mức ước lượng TA/TR; không chấm ngữ pháp.")
                if not overall_summary:
                    overall_summary = "Đã rà soát từ vựng và mức độ sát đề. Ngữ pháp không thuộc phạm vi đánh giá này."
                normalized = {
                    "overall_summary": overall_summary,
                    "question_analysis": question_analysis,
                    "criterion_estimate": criterion_estimate,
                    "idea_development": clean_development,
                    "field_reviews": clean_field_reviews,
                    "fill_required_blanks": clean_fill,
                    "summary_categories": summary_categories
                }
                return self.send_json(200, {"review": normalized, "model": model, "criterion": criterion, "rubricFocus": "question coverage, idea relevance and development depth, with a provisional TA/TR estimate; grammar excluded"})
            except RuntimeError as e:
                message = str(e)
                code = 503 if "GEMINI_API_KEY" in message else (429 if "quota" in message.lower() else 502)
                return self.send_json(code, {"error": message})
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
            image_data = data.get("image_data") if isinstance(data.get("image_data"), str) else ""
            image_url = data.get("image_url") if isinstance(data.get("image_url"), str) else ""
            if task == "Task 1" and not image_data.startswith("data:image/") and image_url:
                image_data = fetch_task1_image_as_data_url(image_url, base_dir=BASE)
            image_note = "\n\nĐÃ ĐÍNH KÈM ẢNH ĐỀ TASK 1: hãy quan sát biểu đồ/bản đồ trực tiếp, xác định đúng đơn vị, hạng mục và số liệu trước khi đánh giá mọi câu mô tả. Nếu ảnh không hiển thị rõ, nói rõ giới hạn; tuyệt đối không bịa số liệu." if task == "Task 1" and image_data.startswith("data:image/") else ("\n\nKHÔNG CÓ ẢNH ĐỀ ĐƯỢC GỬI: không được khẳng định đã đối chiếu số liệu trực quan; chỉ đánh giá ngôn ngữ và nội dung dựa trên đề chữ." if task == "Task 1" else "")
            prompt = "Hãy chấm bài theo rubric và JSON schema trong system instruction.\nTASK: " + task_note + "\nĐỀ BÀI:\n" + question.strip() + "\n\nBÀI VIẾT NGUYÊN VĂN:\n" + essay_text.strip() + f"\n\nSố từ tự đếm phía máy chủ: {word_count}. Hãy tự kiểm tra lại nếu cần." + image_note
            try:
                feedback, model = ai_generate(rubric, prompt, max_output_tokens=9000, image_data=image_data)
                feedback = normalize_writing_feedback(feedback, task, word_count)
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
        if path == "/api/history":
            user = self.session_user()
            if not user: return self.send_json(401, {"error": "Bạn cần đăng nhập."})
            with db() as c:
                c.execute("DELETE FROM user_history WHERE user_id=?", (user["id"],)); invalidate_leaderboard_cache()
            return self.send_json(200, {"ok": True, "deleted": "all"})
        note_match = re.fullmatch(r"/api/notes/([^/]+)", path)
        if note_match:
            user = self.session_user()
            if not user: return self.send_json(401, {"error": "Bạn cần đăng nhập để xóa ghi chú đã đồng bộ."})
            note_id = unquote(note_match.group(1))[:120]
            with db() as c:
                c.execute("DELETE FROM user_notes WHERE user_id=? AND note_id=?", (user["id"], note_id))
            return self.send_json(200, {"ok": True, "deleted": note_id})
        history_match = re.fullmatch(r"/api/history/([^/]+)", path)
        if history_match:
            user = self.session_user()
            if not user: return self.send_json(401, {"error": "Bạn cần đăng nhập."})
            record_id = unquote(history_match.group(1))[:120]
            with db() as c:
                c.execute("DELETE FROM user_history WHERE user_id=? AND record_id=?", (user["id"], record_id)); invalidate_leaderboard_cache()
            return self.send_json(200, {"ok": True, "deleted": record_id})
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
        # Compatibility for old tabs: migrate any embedded history into its own append-only table.
        if isinstance(state.get("history"), list):
            with db() as c:
                upsert_user_history(c, user["id"], state.get("history", []))
        # History is stored in user_history, not in the mutable whole-account JSON snapshot.
        state = {k: state.get(k) for k in ("profile", "answers", "lessonAnswers", "independentDrafts", "brainstormDrafts", "currentLessonId", "lessonProgress", "mistakes", "customLessons", "studyDraft") if k in state}
        progress = state.get("lessonProgress")
        if isinstance(progress, dict):
            clean_progress = {}
            for lesson_id, entry in list(progress.items())[:2000]:
                if not isinstance(lesson_id, str) or not lesson_id or len(lesson_id) > 200 or not isinstance(entry, dict): continue
                attempts = entry.get("scoredAttempts", [])
                clean_attempts = []
                if isinstance(attempts, list):
                    for attempt in attempts[-1000:]:
                        if not isinstance(attempt, dict): continue
                        record_id = str(attempt.get("recordId", ""))[:80]
                        try: score = float(attempt.get("score"))
                        except (TypeError, ValueError): continue
                        if not record_id or not 0 <= score <= 9: continue
                        clean_attempts.append({"recordId": record_id, "score": score, "corrected": bool(attempt.get("corrected", False)), "date": str(attempt.get("date", ""))[:40]})
                clean_progress[lesson_id] = {"attempted": bool(entry.get("attempted", False)), "scoredAttempts": clean_attempts, "lastAttemptAt": str(entry.get("lastAttemptAt", ""))[:40]}
            state["lessonProgress"] = clean_progress
        else:
            state["lessonProgress"] = {}
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
    print(f"WriteWiser v47 running at http://{HOST}:{PORT} ({'PostgreSQL' if DATABASE_URL else 'SQLite local'})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping WriteWiser v47.")
    finally:
        server.server_close()

if __name__ == "__main__":
    main()
