#!/usr/bin/env python3
"""WriteWiser v47 — IELTS Writing Band Descriptors Rubric Builder, Normalizer & Analytics."""
import json, re
from pathlib import Path
from ww_db import BASE, db

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
    lines.extend(["", "QUY TẮC ĐẦU RA BẮT BUỘC CỦA WRITEWISER: điểm của từng tiêu chí phải là SỐ NGUYÊN từ 0 đến 9, tuyệt đối không dùng .5. Overall được tính trung bình bốn tiêu chí và làm tròn tới band 0.5 gần nhất."])
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
    # This final contract intentionally appears after the user-supplied master prompt
    # so the model receives one stable output schema even when older prompt text
    # contains a different JSON example or Markdown-oriented formatting requests.
    lines.extend([
        "", "=== WRITEWISER v36 — JSON OUTPUT CONTRACT OVERRIDE (ƯU TIÊN CAO NHẤT) ===",
        "Chỉ trả về MỘT JSON object hợp lệ, không Markdown fence, không bảng Markdown bên ngoài JSON và không có lời dẫn ngoài JSON.",
        "Bắt buộc có đúng 4 phần tử criteria theo thứ tự: Task Achievement (Task 1) hoặc Task Response (Task 2), Coherence and Cohesion, Lexical Resource, Grammatical Range and Accuracy. Band từng tiêu chí là số nguyên 0–9; server sẽ tính overall_band từ bốn band đó.",
        "Schema chuẩn: overall_band (number), word_count (integer), task_criterion ('TA' hoặc 'TR'), criteria[{name,band,rationale,strengths[],weaknesses[],evidence[],improvements[],next_band_gap}], overall_summary, strengths[], priority_errors[{original,correction,explanation,criterion}], vocabulary_review[{original,correction,explanation,issue_type}], grammar_review[{original,correction,explanation}], paragraph_feedback[{paragraph,comment,suggestions[]}], upgraded_version, next_steps[], additional_notes[], caveat.",
        "Mỗi tiêu chí phải có rationale, strengths, weaknesses, evidence và improvements. Evidence phải trích nguyên văn từ bài. Mảng vocabulary_review và grammar_review là OPTIONAL: trả [] nếu không có ví dụ phù hợp, không tạo lỗi giả và không ép mục nào phải có nội dung. paragraph_feedback, upgraded_version, next_steps và additional_notes cũng có thể rỗng hoặc [] nếu không hữu ích cho bài cụ thể.",
        "Trong vocabulary_review, original phải là cụm nguyên văn từ bài viết; correction là bản sửa đề xuất; explanation giải thích ngắn về collocation, word choice, word form hoặc spelling. Đây là dữ liệu để frontend dựng bảng 'Cách viết trong bài / Cách sửa phù hợp / Giải thích', không trả dữ liệu dạng Markdown table.",
        "Trong grammar_review, original là câu/cụm nguyên văn, correction là bản sửa, explanation giải thích cấu trúc hoặc nguyên tắc. Các lỗi trọng yếu không thuộc từ vựng có thể được đặt trong priority_errors. Hạn chế trùng lặp giữa các phần.",
        "Dùng chuỗi rỗng cho trường text không áp dụng, [] cho mảng rỗng. Không điền 'N/A', không bịa lỗi hoặc nội dung để lấp biểu mẫu. caveat mặc định: 'Điểm ước lượng dựa trên bài viết và các tiêu chí IELTS Writing, không phải điểm từ giám khảo IELTS chính thức.'",
        'JSON template: {"overall_band":6.0,"word_count":250,"task_criterion":"TR","criteria":[{"name":"Task Response","band":6,"rationale":"...","strengths":["..."],"weaknesses":["..."],"evidence":["exact quote"],"improvements":["..."],"next_band_gap":"..."},{"name":"Coherence and Cohesion","band":6,"rationale":"...","strengths":[],"weaknesses":[],"evidence":[],"improvements":[],"next_band_gap":""},{"name":"Lexical Resource","band":6,"rationale":"...","strengths":[],"weaknesses":[],"evidence":[],"improvements":[],"next_band_gap":""},{"name":"Grammatical Range and Accuracy","band":5,"rationale":"...","strengths":[],"weaknesses":[],"evidence":[],"improvements":[],"next_band_gap":""}],"overall_summary":"...","strengths":[],"priority_errors":[],"vocabulary_review":[],"grammar_review":[],"paragraph_feedback":[],"upgraded_version":"","next_steps":[],"additional_notes":[],"caveat":"Điểm ước lượng dựa trên bài viết và các tiêu chí IELTS Writing, không phải điểm từ giám khảo IELTS chính thức."}'
    ])
    return "\n".join(lines)

def _as_json_list(value):
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def normalize_writing_feedback(payload, task, word_count):
    """Normalize provider variations to the stable WriteWiser v36 scoring schema."""
    if not isinstance(payload, dict):
        return {}
    feedback = dict(payload)
    aliases = {
        "overall_summary": ("summary", "overall_comment", "summary_overall"),
        "strengths": ("overall_strengths", "positive_points"),
        "priority_errors": ("errors", "corrections", "important_errors"),
        "vocabulary_review": ("lexical_review", "lexical_corrections", "vocabulary_corrections", "lexical_items", "vocabulary_items", "vocabulary_table", "lexical_table"),
        "grammar_review": ("grammar_corrections", "grammatical_corrections", "grammar_items", "grammar_examples"),
        "paragraph_feedback": ("paragraph_review", "paragraphs", "paragraph_comments"),
        "upgraded_version": ("reference_essay", "improved_essay", "rewritten_essay", "model_answer"),
        "next_steps": ("improvement_plan", "priorities", "practice_steps"),
        "additional_notes": ("notes", "cautions", "extra_notes"),
        "caveat": ("disclaimer", "score_note"),
    }
    for canonical, alt_names in aliases.items():
        if canonical not in feedback or feedback[canonical] is None:
            for alt in alt_names:
                if alt in feedback and feedback[alt] is not None:
                    feedback[canonical] = feedback[alt]
                    break
    expected_names = ["Task Achievement" if task == "Task 1" else "Task Response", "Coherence and Cohesion", "Lexical Resource", "Grammatical Range and Accuracy"]
    raw_criteria = feedback.get("criteria", feedback.get("criterion_scores", feedback.get("scores", [])))
    if isinstance(raw_criteria, dict):
        normalized_criteria = []
        for index, expected in enumerate(expected_names):
            code = ["TA" if task == "Task 1" else "TR", "CC", "LR", "GRA"][index]
            match = next((v for k, v in raw_criteria.items() if str(k).strip().lower() in {expected.lower(), code.lower(), ("task achievement" if code=="TA" else "task response")}), None)
            if isinstance(match, dict):
                item = dict(match)
            else:
                item = {"band": match}
            item.setdefault("name", expected)
            normalized_criteria.append(item)
        raw_criteria = normalized_criteria
    criteria = []
    if isinstance(raw_criteria, list):
        for index, expected in enumerate(expected_names):
            raw = raw_criteria[index] if index < len(raw_criteria) else {}
            if not isinstance(raw, dict):
                raw = {"band": raw}
            item = dict(raw)
            if not item.get("name") and item.get("criterion"):
                item["name"] = item["criterion"]
            item["name"] = expected
            if "band" not in item and "score" in item:
                item["band"] = item.get("score")
            if "rationale" not in item:
                item["rationale"] = item.get("comment", item.get("explanation", ""))
            for key, alts in {
                "strengths": ("positive_points", "good_points", "what_works"),
                "weaknesses": ("areas_to_improve", "improvements_needed", "issues"),
                "evidence": ("examples", "quotes", "evidence_from_essay"),
                "improvements": ("suggestions", "recommendations", "corrections"),
            }.items():
                if key not in item:
                    for alt in alts:
                        if alt in item:
                            item[key] = item[alt]
                            break
                item[key] = _as_json_list(item.get(key))
            item["next_band_gap"] = str(item.get("next_band_gap", item.get("gap_to_next_band", "")) or "")
            criteria.append(item)
    feedback["criteria"] = criteria
    feedback["task_criterion"] = "TA" if task == "Task 1" else "TR"
    feedback["word_count"] = word_count
    feedback["overall_summary"] = str(feedback.get("overall_summary", "") or "")
    feedback["strengths"] = _as_json_list(feedback.get("strengths"))
    # Structured correction sections should be arrays of objects. If a provider
    # ignores the schema and emits a prose string, preserve that prose as an optional
    # note rather than rendering an empty row in a correction table.
    freeform_notes = _as_json_list(feedback.get("additional_notes"))
    for key in ("priority_errors", "vocabulary_review", "grammar_review", "paragraph_feedback", "next_steps", "additional_notes"):
        raw_value = feedback.get(key)
        if key in ("priority_errors", "vocabulary_review", "grammar_review") and isinstance(raw_value, str) and raw_value.strip():
            freeform_notes.append(f"{key}: {raw_value.strip()}")
            items = []
        else:
            items = _as_json_list(raw_value)
        normalized_items = []
        for item in items:
            if isinstance(item, str):
                if key in ("priority_errors", "vocabulary_review", "grammar_review"):
                    normalized_items.append({"original": item, "correction": "", "explanation": ""})
                elif key == "paragraph_feedback":
                    normalized_items.append({"paragraph": "Đoạn văn", "comment": item, "suggestions": []})
                else:
                    normalized_items.append(item)
            elif isinstance(item, dict):
                obj = dict(item)
                if key in ("priority_errors", "vocabulary_review", "grammar_review"):
                    obj.setdefault("original", obj.get("before", obj.get("original_text", obj.get("phrase", ""))))
                    obj.setdefault("correction", obj.get("after", obj.get("suggestion", obj.get("revised", ""))))
                    obj.setdefault("explanation", obj.get("reason", obj.get("why", "")))
                elif key == "paragraph_feedback":
                    obj.setdefault("paragraph", obj.get("name", obj.get("section", "Đoạn văn")))
                    obj.setdefault("comment", obj.get("feedback", obj.get("analysis", "")))
                    obj["suggestions"] = _as_json_list(obj.get("suggestions", obj.get("recommendations", [])))
                normalized_items.append(obj)
        feedback[key] = normalized_items
    feedback["additional_notes"] = [x for x in freeform_notes if isinstance(x, str) and x.strip()]
    feedback["upgraded_version"] = str(feedback.get("upgraded_version", "") or "")
    feedback["caveat"] = str(feedback.get("caveat", "") or "Điểm ước lượng dựa trên bài viết và các tiêu chí IELTS Writing, không phải điểm từ giám khảo IELTS chính thức.")
    return feedback


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

