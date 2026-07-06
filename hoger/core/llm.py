"""
hoger/core/llm.py — 可選的 LLM 深度解讀（task v3-B）。

v3-A 的 describe.py 是規則式、確定性、不呼叫任何外部服務的基礎層。本模組
是**選用**的加值層：使用者在掃描勾選階段勾選「AI 深度解讀」時，把
describe.build_graph_digest() 產生的結構事實送給可插拔的 LLM provider，
取得「這個定義在做什麼」的語意理解與逐參數描述。

設計原則：
- **可插拔 provider**：gemini-cli（本機 `gemini` CLI，零設定，免費版）｜
  gemini-api｜anthropic｜openai｜ollama。全部走 `requests`（gemini-cli 走
  `subprocess`），不新增 SDK 依賴。
- **無聲 fallback**：呼叫端（hoger/api/routes.py）在 LlmError/逾時時捕捉
  例外，規則式描述原樣保留，只在回應加一個警告欄位——本模組不負責
  fallback 邏輯本身，只負責「失敗就明確拋 LlmError」。
- **環境變數即時讀取**：所有設定透過 os.environ.get() 在呼叫當下讀取
  （不是模組載入時快取的常數），這樣單元測試用 monkeypatch.setenv 才能
  正確覆寫每個 provider 分支的行為。hoger.config 裡對應的常數是給非
  llm.py 的其他程式碼讀的「啟動時快照」，這裡刻意不重用它們。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field

import requests

# ── 設定讀取（呼叫當下即時讀取環境變數，見模組 docstring） ───────────


def _provider() -> str:
    return os.environ.get("HOGER_LLM_PROVIDER", "gemini-cli")


def _model_override() -> str | None:
    return os.environ.get("HOGER_LLM_MODEL") or None


def _gemini_api_key() -> str:
    return os.environ.get("HOGER_GEMINI_API_KEY", "")


def _anthropic_api_key() -> str:
    return os.environ.get("HOGER_ANTHROPIC_API_KEY", "")


def _openai_api_key() -> str:
    return os.environ.get("HOGER_OPENAI_API_KEY", "")


def _ollama_url() -> str:
    return os.environ.get("HOGER_OLLAMA_URL", "http://localhost:11434")


def _timeout() -> int:
    return int(os.environ.get("HOGER_LLM_TIMEOUT", "120"))


# 各 provider 的預設 model id。查證結論（2026-07 web search）：
# - gemini-2.5-flash：官方文件確認的穩定 generateContent model id
#   （"gemini-3.5-flash" 較新且以 alias 形式存在，穩定性未如
#   gemini-2.5-flash 明確，故採用查證門檻較高的選項）。
# - claude-haiku-4-5：Anthropic 目前的輕量款 model id（不含日期後綴）。
# - gpt-4o-mini：OpenAI 廣泛文件化、穩定的輕量款 model id。
_DEFAULT_MODELS: dict[str, str] = {
    "gemini-api": "gemini-2.5-flash",
    "anthropic": "claude-haiku-4-5",
    "openai": "gpt-4o-mini",
}


class LlmError(RuntimeError):
    """LLM 呼叫失敗（無 key/CLI 不存在/逾時/回應無法解析）時拋出。呼叫端 fallback。"""


@dataclass
class LlmStatus:
    provider: str
    model: str
    available: bool
    reason: str = ""  # 不可用原因；available=True 時為空字串


@dataclass
class Interpretation:
    tool_purpose: str = ""
    param_descriptions: dict[str, str] = field(default_factory=dict)
    output_descriptions: dict[str, str] = field(default_factory=dict)
    usage_notes: str = ""


# ── status() ─────────────────────────────────────────────────────────


def status() -> LlmStatus:
    """回報目前設定的 provider 是否可用，供 UI 顯示與呼叫端提前判斷。"""
    provider = _provider()
    model = _model_override() or _DEFAULT_MODELS.get(provider, "")

    if provider == "gemini-cli":
        found = shutil.which("gemini")
        if found:
            return LlmStatus(provider=provider, model=model or "gemini-cli", available=True)
        return LlmStatus(
            provider=provider,
            model=model or "gemini-cli",
            available=False,
            reason="未偵測到 gemini CLI，請安裝 Gemini CLI 或改用其他 provider",
        )

    if provider == "gemini-api":
        if _gemini_api_key():
            return LlmStatus(provider=provider, model=model, available=True)
        return LlmStatus(
            provider=provider,
            model=model,
            available=False,
            reason="未設定 HOGER_GEMINI_API_KEY",
        )

    if provider == "anthropic":
        if _anthropic_api_key():
            return LlmStatus(provider=provider, model=model, available=True)
        return LlmStatus(
            provider=provider,
            model=model,
            available=False,
            reason="未設定 HOGER_ANTHROPIC_API_KEY",
        )

    if provider == "openai":
        if _openai_api_key():
            return LlmStatus(provider=provider, model=model, available=True)
        return LlmStatus(
            provider=provider,
            model=model,
            available=False,
            reason="未設定 HOGER_OPENAI_API_KEY",
        )

    if provider == "ollama":
        # 不主動探測（避免啟動時網路呼叫）——設定 provider=ollama 即視為可用。
        model_name = _model_override() or ""
        return LlmStatus(provider=provider, model=model_name, available=True)

    return LlmStatus(
        provider=provider,
        model=model,
        available=False,
        reason=f"未知的 provider: {provider!r}",
    )


# ── estimate_tokens() ────────────────────────────────────────────────


def estimate_tokens(digest: str) -> int:
    """粗估 token 數：CJK 字元約 1 字/token，其餘（ASCII 等）約 4 字/token。

    這只是粗估（用於 UI 的大檔案警告），不是精確計算——真正的 tokenizer
    因 provider 而異，這裡用簡單的字元加權近似即可。
    """
    if not digest:
        return 0
    cjk_count = sum(1 for ch in digest if _is_cjk(ch))
    other_count = len(digest) - cjk_count
    return round(cjk_count * 1.0 + other_count / 4.0)


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return (
        0x4E00 <= code <= 0x9FFF  # CJK Unified Ideographs
        or 0x3040 <= code <= 0x30FF  # Hiragana/Katakana
        or 0xAC00 <= code <= 0xD7A3  # Hangul
    )


# ── JSON extraction ──────────────────────────────────────────────────

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(raw: str) -> dict:
    """從 LLM 輸出擷取第一個合法 JSON 物件。

    嘗試順序：整段直接解析 -> 剝除 ```json 圍欄 -> 找第一個 {...} 區塊。
    全部失敗則 raise LlmError。缺欄位（tool_purpose/param_descriptions/
    output_descriptions/usage_notes）一律補空值，不因為 LLM 少回一個欄位
    就整個失敗。
    """
    candidates = []

    stripped = raw.strip()
    candidates.append(stripped)

    fence_match = _JSON_FENCE_RE.search(raw)
    if fence_match:
        candidates.append(fence_match.group(1))

    obj_match = _JSON_OBJECT_RE.search(raw)
    if obj_match:
        candidates.append(obj_match.group(0))

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            return {
                "tool_purpose": parsed.get("tool_purpose") or "",
                "param_descriptions": parsed.get("param_descriptions") or {},
                "output_descriptions": parsed.get("output_descriptions") or {},
                "usage_notes": parsed.get("usage_notes") or "",
            }

    raise LlmError(f"無法從 LLM 回應擷取合法 JSON：{raw[:200]!r}")


# ── prompt building ──────────────────────────────────────────────────


def _build_prompt(digest: str, param_names: list[str], output_names: list[str]) -> str:
    return f"""你是資深 Grasshopper / Rhino.Compute 顧問。以下是一個工具定義的結構事實
（由程式碼靜態解析 GH 檔案取得，不是你自己讀取的原始檔案）：

{digest}

請根據以上事實，理解這個定義在做什麼，並用嚴格的 JSON 格式回答（不要加任何
JSON 以外的文字、不要用 markdown 圍欄），格式如下：

{{
  "tool_purpose": "這個定義在做什麼，2-4 句繁體中文",
  "param_descriptions": {{"參數名": "這個參數影響什麼，繁體中文"}},
  "output_descriptions": {{"輸出名": "這個輸出代表什麼，繁體中文"}},
  "usage_notes": "給 AI 呼叫端的補充說明，可留空字串"
}}

param_descriptions 的 key 必須完全對應以下輸入參數名稱：{param_names}
output_descriptions 的 key 必須完全對應以下輸出名稱：{output_names}
"""


# ── interpret() ──────────────────────────────────────────────────────


def interpret(digest: str, param_names: list[str], output_names: list[str]) -> Interpretation:
    """把 digest 送給目前設定的 provider，取得語意解讀。失敗一律 raise LlmError。"""
    provider = _provider()
    prompt = _build_prompt(digest, param_names, output_names)

    if provider == "gemini-cli":
        raw = _call_gemini_cli(prompt)
    elif provider == "gemini-api":
        raw = _call_gemini_api(prompt)
    elif provider == "anthropic":
        raw = _call_anthropic(prompt)
    elif provider == "openai":
        raw = _call_openai(prompt)
    elif provider == "ollama":
        raw = _call_ollama(prompt)
    else:
        raise LlmError(f"未知的 LLM provider: {provider!r}")

    parsed = _extract_json(raw)
    return Interpretation(**parsed)


# ── provider: gemini-cli ─────────────────────────────────────────────


def _call_gemini_cli(prompt: str) -> str:
    gemini_path = shutil.which("gemini")
    if not gemini_path:
        raise LlmError("未偵測到 gemini CLI（shutil.which('gemini') 找不到）")

    try:
        result = subprocess.run(
            [gemini_path, "-y", "-p", prompt],
            capture_output=True,
            timeout=_timeout(),
            encoding="utf-8",
        )
    except subprocess.TimeoutExpired as exc:
        raise LlmError(f"gemini CLI 逾時（{_timeout()}s）") from exc
    except OSError as exc:
        raise LlmError(f"gemini CLI 執行失敗：{exc}") from exc

    if result.returncode != 0:
        raise LlmError(f"gemini CLI 回傳非零狀態碼 {result.returncode}：{result.stderr[:500]}")

    return result.stdout or ""


# ── provider: gemini-api ─────────────────────────────────────────────


def _call_gemini_api(prompt: str) -> str:
    api_key = _gemini_api_key()
    if not api_key:
        raise LlmError("未設定 HOGER_GEMINI_API_KEY")

    model = _model_override() or _DEFAULT_MODELS["gemini-api"]
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        f"?key={api_key}"
    )
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    try:
        resp = requests.post(url, json=payload, timeout=_timeout())
    except requests.exceptions.RequestException as exc:
        raise LlmError(f"Gemini API 連線失敗：{exc}") from exc

    if resp.status_code >= 400:
        raise LlmError(f"Gemini API HTTP {resp.status_code}: {resp.text[:500]}")

    try:
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise LlmError(f"無法解析 Gemini API 回應：{exc}") from exc


# ── provider: anthropic ──────────────────────────────────────────────


def _call_anthropic(prompt: str) -> str:
    api_key = _anthropic_api_key()
    if not api_key:
        raise LlmError("未設定 HOGER_ANTHROPIC_API_KEY")

    model = _model_override() or _DEFAULT_MODELS["anthropic"]
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": model,
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": prompt}],
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=_timeout())
    except requests.exceptions.RequestException as exc:
        raise LlmError(f"Anthropic API 連線失敗：{exc}") from exc

    if resp.status_code >= 400:
        raise LlmError(f"Anthropic API HTTP {resp.status_code}: {resp.text[:500]}")

    try:
        data = resp.json()
        return data["content"][0]["text"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise LlmError(f"無法解析 Anthropic API 回應：{exc}") from exc


# ── provider: openai ─────────────────────────────────────────────────


def _call_openai(prompt: str) -> str:
    api_key = _openai_api_key()
    if not api_key:
        raise LlmError("未設定 HOGER_OPENAI_API_KEY")

    model = _model_override() or _DEFAULT_MODELS["openai"]
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "content-type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=_timeout())
    except requests.exceptions.RequestException as exc:
        raise LlmError(f"OpenAI API 連線失敗：{exc}") from exc

    if resp.status_code >= 400:
        raise LlmError(f"OpenAI API HTTP {resp.status_code}: {resp.text[:500]}")

    try:
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise LlmError(f"無法解析 OpenAI API 回應：{exc}") from exc


# ── provider: ollama ─────────────────────────────────────────────────


def _call_ollama(prompt: str) -> str:
    model = _model_override()
    if not model:
        raise LlmError("ollama provider 需要設定 HOGER_LLM_MODEL")

    url = _ollama_url().rstrip("/") + "/api/generate"
    payload = {"model": model, "prompt": prompt, "stream": False}

    try:
        resp = requests.post(url, json=payload, timeout=_timeout())
    except requests.exceptions.RequestException as exc:
        raise LlmError(f"Ollama 連線失敗：{exc}") from exc

    if resp.status_code >= 400:
        raise LlmError(f"Ollama HTTP {resp.status_code}: {resp.text[:500]}")

    try:
        data = resp.json()
        return data["response"]
    except (KeyError, TypeError, ValueError) as exc:
        raise LlmError(f"無法解析 Ollama 回應：{exc}") from exc
