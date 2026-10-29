#!/usr/bin/env python3
"""WriteWiser v47 — Document & Catalog Importer Service (DOL catalog, DOCX, HTML, CSV, JSON)."""
from html.parser import HTMLParser
from pathlib import Path
import urllib.request, zipfile, io, csv, json, re, base64
import xml.etree.ElementTree as ET

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
    from urllib.parse import urlparse, unquote
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

