#!/usr/bin/env python3
"""WriteWiser v47 — Multi-Provider AI Gateway (Gemini, Groq, Together, OpenRouter, Custom) & Task 1 Image Fetcher."""
import os, re, json, base64, posixpath, urllib.request, urllib.error
from urllib.parse import urlparse, unquote
from pathlib import Path

BASE = Path(__file__).resolve().parent

ALLOWED_IMAGE_HOSTS = {
    "img.dolenglish.vn",
    "tuhoc.dolenglish.vn",
    "dolenglish.vn",
    "www.dolenglish.vn",
    "dolacademy.vn",
    "www.dolacademy.vn",
}


def fetch_remote_or_local_image(image_url, base_dir=BASE):
    """Fetch a whitelisted DOL chart image or local /assets/ image and return (raw_bytes, mime_type)."""
    url = str(image_url or "").strip()
    if not url:
        return None, None
    if url.startswith("/assets/"):
        rel = posixpath.normpath(unquote(url)).lstrip("/")
        target = (base_dir / rel).resolve()
        if not str(target).startswith(str((base_dir / "assets").resolve())) or not target.is_file():
            return None, None
        ext = target.suffix.lower()
        mime = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".svg": "image/svg+xml",
        }.get(ext, "application/octet-stream")
        return target.read_bytes()[:4_000_000], mime
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in ALLOWED_IMAGE_HOSTS:
        return None, None
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; WriteWiser/47)",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Referer": "https://tuhoc.dolenglish.vn/",
        },
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        content_type = (resp.headers.get("Content-Type") or "image/png").split(";")[0].strip().lower()
        if not content_type.startswith("image/"):
            return None, None
        raw = resp.read(4_000_000)
    return raw, content_type


def fetch_task1_image_as_data_url(image_url, base_dir=BASE):
    """Convert an external DOL chart URL into a base64 data URL on the server (bypasses browser CORS)."""
    try:
        raw, mime = fetch_remote_or_local_image(image_url, base_dir=base_dir)
        if not raw or not mime or mime == "image/svg+xml":
            return ""
        return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")
    except Exception:
        return ""


def gemini_generate(system_instruction, prompt, max_output_tokens=6500, image_data=None):
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Chưa cấu hình GEMINI_API_KEY. Hãy tạo key trong Google AI Studio và thiết lập biến môi trường.")
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{3,100}", model): model="gemini-2.5-flash"
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
        "User-Agent":os.environ.get("WRITEWISER_USER_AGENT", "WriteWiser/47 (IELTS Writing learning app; contact https://www.facebook.com/people/WriteWiser/61595026757886/)").strip(),
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

