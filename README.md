# WriteWiser v25 — footer bản quyền và liên hệ trên nền tảng v13

Bản này kế thừa ứng dụng v11 và bổ sung adapter PostgreSQL để triển khai nhiều người dùng trên hosting từ xa.

## Kiến trúc
- Render Free Web Service chạy Python HTTP server; `render.yaml` khai báo build/start/health check.
- Supabase Free PostgreSQL lưu users, sessions, user_state. Khi có `DATABASE_URL`, server dùng PostgreSQL; nếu không có, chạy SQLite cục bộ như trước.
- `GEMINI_API_KEY` và `DATABASE_URL` chỉ đặt trong Environment của Render, không đưa vào frontend hoặc GitHub.
- Có giới hạn tốc độ cơ bản theo IP cho đăng ký, đăng nhập và các endpoint gọi AI. Đây là lớp bảo vệ cơ bản cho single-instance free hosting, không thay thế hệ thống chống lạm dụng production.
- Secure cookie bật qua `DRAFTWISE_SECURE_COOKIE=1`.

## Triển khai miễn phí

### A. Tạo cơ sở dữ liệu Supabase
1. Vào https://supabase.com/ và tạo một project Free.
2. Lưu lại database password an toàn.
3. Mở **Connect** trong dashboard, chọn **Transaction pooler** và sao chép connection string PostgreSQL. Dùng chuỗi URI có `sslmode=require` nếu được cung cấp. Không đưa chuỗi này vào mã nguồn hoặc repo.
4. Server tự tạo bảng `users`, `sessions`, `user_state` khi khởi động lần đầu.

### B. Đưa mã nguồn lên GitHub
1. Tạo repository mới, ví dụ `draftwise`.
2. Upload các file/thư mục trong gói v13. Không upload `.env`, `draftwise.sqlite3`, API key hoặc database password.
3. Đảm bảo `render.yaml`, `requirements.txt`, `server.py`, `index.html`, `app.js`, `styles.css`, `assets/` và `resources/` nằm ở thư mục gốc repository.

### C. Deploy trên Render
1. Vào https://render.com/ và kết nối GitHub.
2. Chọn **New → Blueprint** rồi chọn repository có `render.yaml` (hoặc tạo Web Service và dùng build/start commands bên dưới).
3. Đặt các environment variables được hỏi:
   - `DATABASE_URL`: connection string Supabase Transaction pooler.
   - `GEMINI_API_KEY`: API key của bạn.
   - `GEMINI_MODEL`: `gemini-3.8-flash` (có thể đổi sang model được tài khoản/API của bạn hỗ trợ).
   - `DRAFTWISE_HOST`: `0.0.0.0`.
   - `DRAFTWISE_SECURE_COOKIE`: `1`.
   - `WRITEWISER_GATE_PASSWORD`: mật khẩu truy cập chung, nên dài ít nhất 12 ký tự.
   - `WRITEWISER_ADMIN_USERNAME`: `admin` (hoặc tên đăng nhập bạn chọn).
   - `WRITEWISER_ADMIN_PASSWORD`: mật khẩu admin, nên dài ít nhất 12 ký tự.
4. Nếu tạo Web Service thủ công:
   - Runtime: Python
   - Build command: `pip install -r requirements.txt`
   - Start command: `python server.py`
   - Health check path: `/api/health`
   - Plan: Free
5. Chờ deploy hoàn tất, mở URL `https://<ten-dich-vu>.onrender.com`, rồi kiểm tra `https://<ten-dich-vu>.onrender.com/api/health`. Trường `database` phải là `postgres`, và `aiConfigured` phải là `true`.

## Chuyển dữ liệu từ SQLite cũ (tùy chọn)
Nếu muốn giữ tài khoản/lịch sử ở file `draftwise.sqlite3` cũ:
1. Sao chép file cũ vào thư mục v13 trên máy của bạn (không commit lên GitHub).
2. Cài dependency: `pip install -r requirements.txt`.
3. Đặt biến `DATABASE_URL` thành connection string Supabase. Trên PowerShell:
   ```powershell
   $env:DATABASE_URL="postgresql://..."
   python migrate_sqlite_to_postgres.py
   ```
4. Chỉ chạy migration một lần vào database mới hoặc sau khi đã hiểu rõ dữ liệu đích. Sao lưu file SQLite trước. Script chuyển users, sessions và user_state; nó không chuyển cache/localStorage nằm riêng trong từng trình duyệt.

## Giới hạn free hosting cần biết
- Render Free có thể spin down sau 15 phút không có traffic; lần truy cập đầu sau đó có thể chậm khoảng một phút. Filesystem của Render Free là ephemeral, vì vậy SQLite không được dùng để lưu dữ liệu cloud.
- Supabase Free có thể pause project sau một tuần không hoạt động; giới hạn và chính sách free tier có thể thay đổi. Kiểm tra dashboard của nhà cung cấp định kỳ.
- Đây là cấu hình hobby/beta để thử với nhiều người, không có SLA/backup production. Với dữ liệu quan trọng, xuất/backup định kỳ và cân nhắc gói trả phí.
- Gemini API có quota riêng. Hosting miễn phí không đồng nghĩa với Gemini API miễn phí không giới hạn. Đặt quota/budget phù hợp trong Google AI Studio/Google Cloud và theo dõi usage.
- Có rate limit cơ bản trong bộ nhớ theo IP (đăng ký: 5/giờ, đăng nhập: 12/phút, AI: 12/phút). Nó sẽ reset khi service restart và không thay thế CAPTCHA, email verification, persistent rate limit hoặc chống bot.
- Mật khẩu được hash bằng PBKDF2-HMAC-SHA256. API key chỉ nằm ở server environment. Không bao giờ commit key vào GitHub.

## Kiểm thử sau deploy
1. `/api/health` trả về `ok: true`, `database: postgres`, `aiConfigured: true`.
2. Đăng ký tài khoản thử bằng mật khẩu dài ít nhất 10 ký tự.
3. Đăng xuất/đăng nhập lại.
4. Lưu một bài, refresh, kiểm tra dữ liệu còn.
5. Mở trang bằng cửa sổ ẩn danh hoặc thiết bị khác để xác nhận dữ liệu tải từ database thay vì localStorage.
6. Nộp một bài ngắn để kiểm tra Gemini API; xem Render Logs nếu lỗi.

## Chạy local như trước
Không đặt `DATABASE_URL` thì server dùng SQLite local. Cài psycopg không bắt buộc nếu chỉ dùng local; nếu chạy `pip install -r requirements.txt` vẫn không sao.
```powershell
$env:GEMINI_API_KEY="YOUR_GEMINI_API_KEY"
$env:GEMINI_MODEL="gemini-3.8-flash"
python server.py
```
Mở http://127.0.0.1:8000.


## v13 — Account isolation and account dialog fixes
- Password length requirement for new accounts: 5–200 characters. Existing password hashes are not changed.
- Existing account state loads from PostgreSQL; accounts with no saved state start from a clean workspace, not another account's browser-local work.
- Logout saves current state before ending session, then clears the active browser workspace. Saved work remains attached to that account in the database.
- `independentDrafts` is now included in server-side state persistence.
- Fixed account modal close button with explicit type=button and close handler; improved responsive modal layout.

Deploy by updating the same GitHub repository connected to Render. Keep existing Supabase database and environment variables.


## Chức năng nền tảng v13 được giữ lại ở bản hiện tại

- Account/session/database, luyện dịch ngược, lịch sử, sổ lỗi, import/export, danh mục DOL và trang hiệu chuẩn chấm band.
- Đồng hồ: Practice đếm lên; Exam đếm ngược Task 1 (15/20/25/30) và Task 2 (35/40/45/50).
- Brainstorm Practice-only dạng mindmap, đề tự động lấy từ bài đang chọn, AI nhận xét mức độ liên quan/bao phủ/chiều sâu và đề xuất sửa ngay trong sơ đồ.
- Giao diện WriteWiser, logo, Almond Wisp / Jojoba / Modern Mocha và sửa lỗi font/kerning tiếng Việt.
- Lưu ý: thư viện dựng sẵn giữ 12 câu trích ngắn đã đối chiếu với 12 trang bài mẫu DOL (7 dạng Task 1 và 5 nhóm dạng Task 2); không còn 36 bài tự biên soạn của nhánh cũ. Xem mục v20 bên dưới để biết về cổng mật khẩu, admin và cách lưu đề theo tài khoản.

## Xác minh prompt chấm bài
Sau khi deploy, mở `/api/scoring-status`. Bản v13 phải báo `masterPromptLoaded: true`, `promptChars` và SHA-256 được trả về động từ `/api/scoring-status`; hãy dùng giá trị thực tế được endpoint trả về để xác minh, và `criteriaBands: integer 0-9`. Đây là xác nhận prompt đã được nạp vào server scoring; nó không bảo đảm AI tuân thủ hoàn hảo mọi chỉ dẫn.


## WriteWiser v20 (nền tảng v13) — bảo vệ truy cập, tài khoản và thư viện

- Đây là bản sửa từ mã nguồn v13, không sử dụng v19 làm nền. Các chức năng luyện dịch ngược, chấm Writing, lịch sử, sổ lỗi, đồng hồ và mindmap của v13 được giữ lại.
- Thư viện dựng sẵn hiện có 12 bài luyện: 7 dạng Task 1 (line, bar, pie, table, process, map, mixed) và 5 bài Task 2, phủ Opinion, Advantages and disadvantages, Two-part, Discussion và Causes/Solutions. Mỗi bài tích hợp một câu ngắn (không quá 25 từ trích nguyên văn từ trang nguồn), kèm bản dịch và liên kết bài DOL gốc. Khi mở bài từ thư viện, ứng dụng vào thẳng chế độ dịch ngược; ảnh gốc được tải từ DOL, có liên kết dự phòng nếu CDN không tải được. Đây là câu trích để luyện dịch ngược, không phải toàn văn bài mẫu. Các bản tự soạn trước đây đã bị loại khỏi danh sách dựng sẵn.
- Đề do người dùng tự nhập sẽ được lưu trong `user_state` thuộc tài khoản hiện tại. Đề do `admin` nhập được lưu trong bảng `global_lessons` và hiển thị chung cho tài khoản đã mở web. Những bài cá nhân không được lấy từ tài khoản khác.
- Các trường Task, dạng bài và chủ đề trong form nhập đề là dropdown; có lựa chọn “Khác” để điền loại/chủ đề không có sẵn.
- Một lớp phủ yêu cầu mật khẩu chung được xác minh phía máy chủ. Mọi API dữ liệu đều bị khóa nếu chưa mở cổng. Admin có thể đổi mật khẩu chung và mật khẩu tài khoản admin trong bảng Quản trị.
- Ở lần khởi động đầu tiên, nếu chưa thiết lập `WRITEWISER_GATE_PASSWORD` và `WRITEWISER_ADMIN_PASSWORD`, server tự tạo mật khẩu ngẫu nhiên và in một lần vào log khởi động. Hãy lưu lại chúng ngay. Có thể đặt các biến môi trường đó trước lần khởi tạo đầu tiên; database chỉ lưu hash PBKDF2 và salt, không lưu mật khẩu dạng rõ. Tài khoản admin mặc định có tên `admin` trừ khi đặt `WRITEWISER_ADMIN_USERNAME`.
- Để dữ liệu tồn tại qua lần truy cập lại, hãy cấu hình `DATABASE_URL` trỏ tới PostgreSQL bền vững khi triển khai cloud. Chế độ SQLite cục bộ lưu tại `draftwise.sqlite3` trong thư mục dự án. Đừng xóa database nếu muốn giữ tài khoản/đề đã lưu.
- Bảo mật mật khẩu cổng là lớp bảo vệ ứng dụng cá nhân, không thay thế HTTPS, bảo vệ hạ tầng hoặc sao lưu cơ sở dữ liệu.


## v22 changes

- Main navigation and mode-choice buttons use direct listeners; lesson-card buttons use delegation because cards are rebuilt during filtering/rendering. The chooser now permits free-writing prompts that do not include a sample essay, while reverse translation still requires sample sentences.
- “Nhập thêm” stays a usable tab before login and presents a login notice without a blocking dialog. The personal lesson manager and the admin shared-library manager are separate, standalone pages; the sidebar can scroll so these links do not disappear on short screens.
- Selecting a library lesson opens a chooser: reverse translation, or free writing with Practice/Exam submodes. Practice/Exam can still be changed above the prompt.
- Account settings are a distinct page and require the current password to change a user's password, including admin.
- `Quản lý đề của tôi` is a standalone account-specific page for editing/deleting a user's own lessons.
- `Quản lý thư viện` is a separate admin-only page for editing/removing built-in lessons and publishing new shared lessons from a dedicated inline form. Shared changes persist in the server database.
- `Bảo mật web` is a separate admin-only page for changing the shared access-gate password.
- Library contains “Đề có sẵn” and “Nhập thêm” tabs; tab clicks continue to work after the library is re-rendered.
- If the app cannot reach the backend, the access gate explains the correct local launch command rather than failing silently. The “Nhập thêm” tab remains visible to signed-out visitors and shows a login notice instead of opening a blocking login dialog, so the tab itself still works.

Relevant APIs include `POST /api/account/password`, `GET/POST /api/library-overrides`, `POST /api/global-lessons` and their protected delete operations.


## Chạy thử trên Windows (khuyến nghị — v24)

1. Giải nén toàn bộ file ZIP vào một thư mục riêng.
2. Mở Terminal/PowerShell ngay trong thư mục dự án đã giải nén (thư mục có `server.py`).
3. Chạy `py -3 server.py` hoặc `python server.py`.
4. Mở `http://127.0.0.1:8000` trong trình duyệt. Không mở `index.html` bằng cách nhấp đúp, vì khi đó API đăng nhập, lưu database và cổng mật khẩu không thể hoạt động đúng.
5. Giữ cửa sổ Terminal mở trong lúc dùng web. Mật khẩu khởi tạo chỉ xuất hiện trong Terminal ở lần tạo database đầu tiên; hãy lưu lại ngay.

Bản v22 tách riêng các trang “Quản lý đề của tôi”, “Quản lý thư viện” (admin) và “Bảo mật web”. Trên trang admin, form tạo đề dùng chung nằm trực tiếp trong trang quản lý. Hai tab thư viện và cửa sổ chọn chế độ được xử lý bằng listener tập trung. Chỉ chạy bằng `python server.py` hoặc `run_writewiser.bat`; không dùng `file://` hay `python -m http.server`.


## WriteWiser v23 — AI dự phòng và hiệu chuẩn

### AI providers
Thiết lập một hoặc nhiều API key dưới dạng biến môi trường; không ghi key vào mã nguồn hoặc ZIP:
- `GEMINI_API_KEY` (tuỳ chọn `GEMINI_MODEL`)
- `GROQ_API_KEY` (tuỳ chọn `GROQ_MODEL`)
- `TOGETHER_API_KEY` (tuỳ chọn `TOGETHER_MODEL`)
- `OPENROUTER_API_KEY` (tuỳ chọn `OPENROUTER_MODEL`)
- `AI_PROVIDER_ORDER=gemini,groq,together,openrouter` để đổi thứ tự dự phòng.

Nếu Gemini trả 503/429, timeout hoặc JSON lỗi, backend thử nhà cung cấp kế tiếp có key đã cấu hình. Mỗi nhà cung cấp có hạn mức, chính sách lưu dữ liệu và giá riêng; dự phòng không đồng nghĩa mọi model đều miễn phí. Kiểm tra điều khoản dữ liệu của nhà cung cấp trước khi gửi bài người học.

### Hiệu chuẩn
- Kết quả hiệu chuẩn cũ trong `localStorage` ở `calibration.html` chỉ tồn tại trên trình duyệt đó và là số liệu đo sai lệch, không tự thay đổi model.
- v23 lưu các hiệu chỉnh sau khi chấm trong database, gắn với tài khoản. Chúng chưa tự động được dùng làm dữ liệu toàn cục.
- Chỉ hiệu chuẩn được admin duyệt trong `admin_calibrations` được thêm (tối đa 3 ví dụ ngắn gần nhất) vào prompt chấm chung. Điều này có thể tăng một lượng nhỏ token đầu vào. Không có fine-tuning model tự động.
- Bài Task 1: giao diện sẽ gửi ảnh hiện tại dưới dạng PNG data URL cho backend khi chấm, nếu ảnh được tải và chuyển đổi thành công. Nếu ảnh không gửi được, model chỉ thấy đề văn bản; kiểm tra phản hồi và log trước khi tin rằng model đã nhìn biểu đồ.

### Database tables bổ sung
`admin_calibrations`, `feedback_corrections`, `reference_overrides`. Tất cả tồn tại trong cùng SQLite cục bộ hoặc PostgreSQL khi `DATABASE_URL` được cấu hình.

## v24 — đồng bộ thư viện đề dùng chung

- Thư viện không hiển thị danh sách đề mẫu trong bundle trước khi cả `/api/global-lessons` và `/api/library-overrides` tải thành công.
- Nếu một trong hai API lỗi, giao diện hiện thông báo lỗi và nút thử lại; không coi API override lỗi là danh sách override rỗng.
- Các override mới nhất được áp dụng sau mỗi lần tải; khi quay lại tab thư viện hoặc tab trình duyệt được mở lại sau một khoảng thời gian, danh sách được đồng bộ lại.
- Sửa phiên bản health endpoint thành `24-library-sync-fix`.


## v25 — disclaimer và liên hệ ở footer

- Thêm tuyên bố miễn trừ trách nhiệm về trích đoạn bài mẫu/đề thi ở chân trang chính và trang hiệu chuẩn.
- Thêm liên kết liên hệ Facebook WriteWiser: https://www.facebook.com/people/WriteWiser/61595026757886/
- Health endpoint hiển thị phiên bản `25-footer-disclaimer`.

## Favicon / logo tab trình duyệt

Các trang `index.html` và `calibration.html` sử dụng biểu tượng WriteWiser từ `favicon.ico` và `assets/favicon-*.png`. Khi đổi logo, cần cập nhật các tệp favicon này; tham số `?v=26` giúp trình duyệt làm mới cache.
