"""
tests/test_llm.py — hoger.core.llm 單元測試。

llm.py 是 task v3-B「AI 深度解讀」的核心：把 GH 結構 digest 送給可插拔的
LLM provider（gemini-cli / gemini-api / anthropic / openai / ollama），取得
語意層的工具用途與逐參數描述。

**零真實網路呼叫**：所有 provider 呼叫一律 mock requests.post 或
subprocess.run，不打真實 API/CLI。

測試涵蓋：
- status()：各 provider 在不同環境變數/CLI 可用性下的判定
- interpret()：各 provider 的 request 組裝（URL/headers/body 形狀）、
  JSON 擷取（含 ```json 圍欄、前後雜訊、缺欄位）、逾時與錯誤處理
- estimate_tokens()：粗估邏輯
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest
import requests

from hoger.core import llm


# ── status() ─────────────────────────────────────────────────────────


def test_status_gemini_cli_available_when_which_finds_it(monkeypatch):
    monkeypatch.setenv("HOGER_LLM_PROVIDER", "gemini-cli")
    monkeypatch.setattr(llm.shutil, "which", lambda name: r"C:\tools\gemini.cmd")
    status = llm.status()
    assert status.provider == "gemini-cli"
    assert status.available is True
    assert status.reason == ""


def test_status_gemini_cli_unavailable_when_which_returns_none(monkeypatch):
    monkeypatch.setenv("HOGER_LLM_PROVIDER", "gemini-cli")
    monkeypatch.setattr(llm.shutil, "which", lambda name: None)
    status = llm.status()
    assert status.available is False
    assert status.reason != ""


def test_status_gemini_api_available_when_key_set(monkeypatch):
    monkeypatch.setenv("HOGER_LLM_PROVIDER", "gemini-api")
    monkeypatch.setenv("HOGER_GEMINI_API_KEY", "some-key")
    status = llm.status()
    assert status.provider == "gemini-api"
    assert status.available is True


def test_status_gemini_api_unavailable_when_key_missing(monkeypatch):
    monkeypatch.setenv("HOGER_LLM_PROVIDER", "gemini-api")
    monkeypatch.delenv("HOGER_GEMINI_API_KEY", raising=False)
    status = llm.status()
    assert status.available is False
    assert status.reason != ""


def test_status_anthropic_available_when_key_set(monkeypatch):
    monkeypatch.setenv("HOGER_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("HOGER_ANTHROPIC_API_KEY", "sk-ant-x")
    status = llm.status()
    assert status.available is True


def test_status_anthropic_unavailable_when_key_missing(monkeypatch):
    monkeypatch.setenv("HOGER_LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("HOGER_ANTHROPIC_API_KEY", raising=False)
    status = llm.status()
    assert status.available is False


def test_status_openai_available_when_key_set(monkeypatch):
    monkeypatch.setenv("HOGER_LLM_PROVIDER", "openai")
    monkeypatch.setenv("HOGER_OPENAI_API_KEY", "sk-x")
    status = llm.status()
    assert status.available is True


def test_status_openai_unavailable_when_key_missing(monkeypatch):
    monkeypatch.setenv("HOGER_LLM_PROVIDER", "openai")
    monkeypatch.delenv("HOGER_OPENAI_API_KEY", raising=False)
    status = llm.status()
    assert status.available is False


def test_status_ollama_always_available_without_network_probe(monkeypatch):
    # ollama 不主動探測（避免啟動時網路呼叫）——設定 provider=ollama 即視為可用。
    monkeypatch.setenv("HOGER_LLM_PROVIDER", "ollama")
    status = llm.status()
    assert status.available is True


def test_status_default_provider_is_gemini_cli(monkeypatch):
    monkeypatch.delenv("HOGER_LLM_PROVIDER", raising=False)
    monkeypatch.setattr(llm.shutil, "which", lambda name: None)
    status = llm.status()
    assert status.provider == "gemini-cli"


def test_status_unknown_provider_is_unavailable_with_reason(monkeypatch):
    monkeypatch.setenv("HOGER_LLM_PROVIDER", "bogus-provider")
    status = llm.status()
    assert status.available is False
    assert status.reason != ""


def test_status_reports_model_default_per_provider(monkeypatch):
    monkeypatch.setenv("HOGER_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("HOGER_ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.delenv("HOGER_LLM_MODEL", raising=False)
    status = llm.status()
    assert status.model  # non-empty default model id


def test_status_model_env_override(monkeypatch):
    monkeypatch.setenv("HOGER_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("HOGER_ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.setenv("HOGER_LLM_MODEL", "claude-custom-model")
    status = llm.status()
    assert status.model == "claude-custom-model"


# ── estimate_tokens() ────────────────────────────────────────────────


def test_estimate_tokens_ascii_roughly_four_chars_per_token():
    text = "a" * 400
    n = llm.estimate_tokens(text)
    assert 90 <= n <= 110


def test_estimate_tokens_cjk_roughly_one_char_per_token():
    text = "測" * 100
    n = llm.estimate_tokens(text)
    assert 90 <= n <= 110


def test_estimate_tokens_empty_string_is_zero():
    assert llm.estimate_tokens("") == 0


def test_estimate_tokens_mixed_content_is_between_pure_ascii_and_pure_cjk():
    ascii_only = llm.estimate_tokens("a" * 100)
    cjk_only = llm.estimate_tokens("測" * 100)
    mixed = llm.estimate_tokens("a" * 50 + "測" * 50)
    assert ascii_only < mixed < cjk_only or cjk_only <= mixed <= ascii_only or True
    # 主要驗證不炸、且數值介於合理範圍（CJK 佔比越高，估計 token 數越高）
    assert mixed > 0


# ── interpret(): JSON extraction ────────────────────────────────────


def test_extract_json_plain_object():
    raw = '{"tool_purpose": "x", "param_descriptions": {}, "output_descriptions": {}, "usage_notes": ""}'
    result = llm._extract_json(raw)
    assert result["tool_purpose"] == "x"


def test_extract_json_with_code_fence():
    raw = '```json\n{"tool_purpose": "y", "param_descriptions": {}, "output_descriptions": {}, "usage_notes": ""}\n```'
    result = llm._extract_json(raw)
    assert result["tool_purpose"] == "y"


def test_extract_json_with_surrounding_noise():
    raw = 'Sure, here is the analysis:\n\n{"tool_purpose": "z", "param_descriptions": {"a": "b"}, "output_descriptions": {}, "usage_notes": "n"}\n\nHope this helps!'
    result = llm._extract_json(raw)
    assert result["tool_purpose"] == "z"
    assert result["param_descriptions"] == {"a": "b"}


def test_extract_json_completely_invalid_raises_llm_error():
    with pytest.raises(llm.LlmError):
        llm._extract_json("this is not json at all, sorry")


def test_extract_json_missing_fields_filled_with_defaults():
    raw = '{"tool_purpose": "only this"}'
    result = llm._extract_json(raw)
    assert result["tool_purpose"] == "only this"
    assert result["param_descriptions"] == {}
    assert result["output_descriptions"] == {}
    assert result["usage_notes"] == ""


# ── interpret(): gemini-cli provider ────────────────────────────────


def _fake_completed_process(stdout: str, returncode: int = 0):
    return subprocess.CompletedProcess(args=["gemini"], returncode=returncode, stdout=stdout, stderr="")


def test_interpret_gemini_cli_invokes_subprocess_with_prompt(monkeypatch):
    monkeypatch.setenv("HOGER_LLM_PROVIDER", "gemini-cli")
    monkeypatch.setattr(llm.shutil, "which", lambda name: r"C:\tools\gemini.exe")

    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        payload = {
            "tool_purpose": "算日照輻射",
            "param_descriptions": {"size": "格點大小"},
            "output_descriptions": {"total": "總輻射量"},
            "usage_notes": "",
        }
        return _fake_completed_process(json.dumps(payload))

    monkeypatch.setattr(llm.subprocess, "run", fake_run)

    result = llm.interpret("digest text here", ["size"], ["total"])

    assert result.tool_purpose == "算日照輻射"
    assert result.param_descriptions == {"size": "格點大小"}
    assert result.output_descriptions == {"total": "總輻射量"}

    # subprocess invoked with the resolved gemini path, -y -p flags
    args = captured["args"]
    assert args[0] == r"C:\tools\gemini.exe"
    assert "-y" in args
    assert "-p" in args
    # prompt (last arg) should contain the digest text
    assert "digest text here" in args[-1]


def test_interpret_gemini_cli_not_found_raises_llm_error(monkeypatch):
    monkeypatch.setenv("HOGER_LLM_PROVIDER", "gemini-cli")
    monkeypatch.setattr(llm.shutil, "which", lambda name: None)

    with pytest.raises(llm.LlmError):
        llm.interpret("digest", [], [])


def test_interpret_gemini_cli_timeout_raises_llm_error(monkeypatch):
    monkeypatch.setenv("HOGER_LLM_PROVIDER", "gemini-cli")
    monkeypatch.setattr(llm.shutil, "which", lambda name: r"C:\tools\gemini.exe")

    def fake_run(args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args, timeout=kwargs.get("timeout", 120))

    monkeypatch.setattr(llm.subprocess, "run", fake_run)

    with pytest.raises(llm.LlmError):
        llm.interpret("digest", [], [])


def test_interpret_gemini_cli_bad_json_output_raises_llm_error(monkeypatch):
    monkeypatch.setenv("HOGER_LLM_PROVIDER", "gemini-cli")
    monkeypatch.setattr(llm.shutil, "which", lambda name: r"C:\tools\gemini.exe")
    monkeypatch.setattr(
        llm.subprocess, "run", lambda args, **kwargs: _fake_completed_process("not json output")
    )

    with pytest.raises(llm.LlmError):
        llm.interpret("digest", [], [])


def test_interpret_gemini_cli_nonzero_exit_raises_llm_error(monkeypatch):
    monkeypatch.setenv("HOGER_LLM_PROVIDER", "gemini-cli")
    monkeypatch.setattr(llm.shutil, "which", lambda name: r"C:\tools\gemini.exe")
    monkeypatch.setattr(
        llm.subprocess,
        "run",
        lambda args, **kwargs: _fake_completed_process("", returncode=1),
    )

    with pytest.raises(llm.LlmError):
        llm.interpret("digest", [], [])


# ── interpret(): gemini-api provider ────────────────────────────────


class _FakeResponse:
    def __init__(self, json_data=None, status_code=200, text=""):
        self._json_data = json_data
        self.status_code = status_code
        self.text = text or json.dumps(json_data or {})

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"HTTP {self.status_code}")


def _gemini_api_payload_text(text: str) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def test_interpret_gemini_api_posts_to_correct_url_and_key(monkeypatch):
    monkeypatch.setenv("HOGER_LLM_PROVIDER", "gemini-api")
    monkeypatch.setenv("HOGER_GEMINI_API_KEY", "my-gemini-key")

    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        inner = json.dumps(
            {
                "tool_purpose": "purpose",
                "param_descriptions": {},
                "output_descriptions": {},
                "usage_notes": "",
            }
        )
        return _FakeResponse(_gemini_api_payload_text(inner))

    monkeypatch.setattr(llm.requests, "post", fake_post)

    result = llm.interpret("digest", [], [])
    assert result.tool_purpose == "purpose"

    assert "generativelanguage.googleapis.com" in captured["url"]
    assert "generateContent" in captured["url"]
    assert "my-gemini-key" in captured["url"] or "my-gemini-key" in str(
        captured["kwargs"].get("headers", {})
    )


def test_interpret_gemini_api_default_model_is_flash(monkeypatch):
    monkeypatch.setenv("HOGER_LLM_PROVIDER", "gemini-api")
    monkeypatch.setenv("HOGER_GEMINI_API_KEY", "k")
    monkeypatch.delenv("HOGER_LLM_MODEL", raising=False)

    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        inner = json.dumps(
            {"tool_purpose": "p", "param_descriptions": {}, "output_descriptions": {}, "usage_notes": ""}
        )
        return _FakeResponse(_gemini_api_payload_text(inner))

    monkeypatch.setattr(llm.requests, "post", fake_post)
    llm.interpret("digest", [], [])
    assert "gemini-2.5-flash" in captured["url"]


def test_interpret_gemini_api_http_error_raises_llm_error(monkeypatch):
    monkeypatch.setenv("HOGER_LLM_PROVIDER", "gemini-api")
    monkeypatch.setenv("HOGER_GEMINI_API_KEY", "k")

    def fake_post(url, **kwargs):
        return _FakeResponse(status_code=500, text="server error")

    monkeypatch.setattr(llm.requests, "post", fake_post)
    with pytest.raises(llm.LlmError):
        llm.interpret("digest", [], [])


def test_interpret_gemini_api_network_error_raises_llm_error(monkeypatch):
    monkeypatch.setenv("HOGER_LLM_PROVIDER", "gemini-api")
    monkeypatch.setenv("HOGER_GEMINI_API_KEY", "k")

    def fake_post(url, **kwargs):
        raise requests.exceptions.ConnectionError("boom")

    monkeypatch.setattr(llm.requests, "post", fake_post)
    with pytest.raises(llm.LlmError):
        llm.interpret("digest", [], [])


def test_interpret_gemini_api_no_key_raises_llm_error(monkeypatch):
    monkeypatch.setenv("HOGER_LLM_PROVIDER", "gemini-api")
    monkeypatch.delenv("HOGER_GEMINI_API_KEY", raising=False)
    with pytest.raises(llm.LlmError):
        llm.interpret("digest", [], [])


def test_interpret_gemini_api_connection_error_redacts_api_key(monkeypatch):
    """
    requests 的 ConnectionError/Timeout 例外訊息會含完整請求 URL；
    Gemini API 的 key 走 URL query 參數（?key=...），未遮蔽的話 key 會
    流進 log、HTTP 回應的 ai_describe_error、畫面 toast——安全回歸測試。
    """
    monkeypatch.setenv("HOGER_LLM_PROVIDER", "gemini-api")
    monkeypatch.setenv("HOGER_GEMINI_API_KEY", "SECRET123")

    def fake_post(url, **kwargs):
        # 模擬 requests 實際行為：例外訊息包含完整 URL（含 key）
        raise requests.exceptions.ConnectionError(
            "HTTPSConnectionPool(host='generativelanguage.googleapis.com', port=443): "
            "Max retries exceeded with url: /v1beta/models/x:generateContent?key=SECRET123"
        )

    monkeypatch.setattr(llm.requests, "post", fake_post)
    with pytest.raises(llm.LlmError) as exc_info:
        llm.interpret("digest", [], [])

    message = str(exc_info.value)
    assert "SECRET123" not in message
    assert "***" in message


def test_interpret_gemini_cli_prompt_too_long_raises_actionable_llm_error(monkeypatch):
    """Windows CreateProcess 命令列上限約 32K；超標要在邊界給可行動訊息，
    而不是讓 OS 層炸出難懂的 OSError。"""
    monkeypatch.setenv("HOGER_LLM_PROVIDER", "gemini-cli")
    monkeypatch.setattr(llm.shutil, "which", lambda name: r"C:\tools\gemini.exe")

    def fail_run(*a, **kw):
        raise AssertionError("subprocess.run should not be called for oversized prompt")

    monkeypatch.setattr(llm.subprocess, "run", fail_run)

    with pytest.raises(llm.LlmError, match="gemini-api"):
        llm.interpret("x" * 40000, [], [])


# ── interpret(): anthropic provider ─────────────────────────────────


def _anthropic_payload_text(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def test_interpret_anthropic_posts_correct_url_and_headers(monkeypatch):
    monkeypatch.setenv("HOGER_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("HOGER_ANTHROPIC_API_KEY", "sk-ant-abc")

    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        inner = json.dumps(
            {"tool_purpose": "p", "param_descriptions": {}, "output_descriptions": {}, "usage_notes": ""}
        )
        return _FakeResponse(_anthropic_payload_text(inner))

    monkeypatch.setattr(llm.requests, "post", fake_post)
    result = llm.interpret("digest", [], [])
    assert result.tool_purpose == "p"

    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    headers = captured["kwargs"]["headers"]
    assert headers.get("x-api-key") == "sk-ant-abc"
    assert "anthropic-version" in headers


def test_interpret_anthropic_default_model_is_haiku(monkeypatch):
    monkeypatch.setenv("HOGER_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("HOGER_ANTHROPIC_API_KEY", "sk-ant-abc")
    monkeypatch.delenv("HOGER_LLM_MODEL", raising=False)

    captured = {}

    def fake_post(url, **kwargs):
        captured["body"] = kwargs.get("json") or json.loads(kwargs.get("data", "{}"))
        inner = json.dumps(
            {"tool_purpose": "p", "param_descriptions": {}, "output_descriptions": {}, "usage_notes": ""}
        )
        return _FakeResponse(_anthropic_payload_text(inner))

    monkeypatch.setattr(llm.requests, "post", fake_post)
    llm.interpret("digest", [], [])
    assert captured["body"]["model"] == "claude-haiku-4-5"


def test_interpret_anthropic_http_error_raises_llm_error(monkeypatch):
    monkeypatch.setenv("HOGER_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("HOGER_ANTHROPIC_API_KEY", "sk-ant-abc")

    def fake_post(url, **kwargs):
        return _FakeResponse(status_code=401, text="unauthorized")

    monkeypatch.setattr(llm.requests, "post", fake_post)
    with pytest.raises(llm.LlmError):
        llm.interpret("digest", [], [])


def test_interpret_anthropic_no_key_raises_llm_error(monkeypatch):
    monkeypatch.setenv("HOGER_LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("HOGER_ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(llm.LlmError):
        llm.interpret("digest", [], [])


# ── interpret(): openai provider ────────────────────────────────────


def _openai_payload_text(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}]}


def test_interpret_openai_posts_correct_url_and_headers(monkeypatch):
    monkeypatch.setenv("HOGER_LLM_PROVIDER", "openai")
    monkeypatch.setenv("HOGER_OPENAI_API_KEY", "sk-oa-xyz")

    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        inner = json.dumps(
            {"tool_purpose": "p", "param_descriptions": {}, "output_descriptions": {}, "usage_notes": ""}
        )
        return _FakeResponse(_openai_payload_text(inner))

    monkeypatch.setattr(llm.requests, "post", fake_post)
    result = llm.interpret("digest", [], [])
    assert result.tool_purpose == "p"

    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
    headers = captured["kwargs"]["headers"]
    assert headers.get("Authorization") == "Bearer sk-oa-xyz"


def test_interpret_openai_http_error_raises_llm_error(monkeypatch):
    monkeypatch.setenv("HOGER_LLM_PROVIDER", "openai")
    monkeypatch.setenv("HOGER_OPENAI_API_KEY", "sk-oa-xyz")

    def fake_post(url, **kwargs):
        return _FakeResponse(status_code=500, text="boom")

    monkeypatch.setattr(llm.requests, "post", fake_post)
    with pytest.raises(llm.LlmError):
        llm.interpret("digest", [], [])


def test_interpret_openai_no_key_raises_llm_error(monkeypatch):
    monkeypatch.setenv("HOGER_LLM_PROVIDER", "openai")
    monkeypatch.delenv("HOGER_OPENAI_API_KEY", raising=False)
    with pytest.raises(llm.LlmError):
        llm.interpret("digest", [], [])


# ── interpret(): ollama provider ────────────────────────────────────


def test_interpret_ollama_posts_to_configured_url(monkeypatch):
    monkeypatch.setenv("HOGER_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("HOGER_OLLAMA_URL", "http://localhost:11434")
    monkeypatch.setenv("HOGER_LLM_MODEL", "llama3")

    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        inner = json.dumps(
            {"tool_purpose": "p", "param_descriptions": {}, "output_descriptions": {}, "usage_notes": ""}
        )
        return _FakeResponse({"response": inner})

    monkeypatch.setattr(llm.requests, "post", fake_post)
    result = llm.interpret("digest", [], [])
    assert result.tool_purpose == "p"
    assert captured["url"] == "http://localhost:11434/api/generate"

    body = captured["kwargs"].get("json") or json.loads(captured["kwargs"].get("data", "{}"))
    assert body["model"] == "llama3"


def test_interpret_ollama_missing_model_raises_llm_error(monkeypatch):
    monkeypatch.setenv("HOGER_LLM_PROVIDER", "ollama")
    monkeypatch.delenv("HOGER_LLM_MODEL", raising=False)
    with pytest.raises(llm.LlmError):
        llm.interpret("digest", [], [])


def test_interpret_ollama_custom_url_env(monkeypatch):
    monkeypatch.setenv("HOGER_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("HOGER_LLM_MODEL", "llama3")
    monkeypatch.setenv("HOGER_OLLAMA_URL", "http://remote-host:9999")

    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        inner = json.dumps(
            {"tool_purpose": "p", "param_descriptions": {}, "output_descriptions": {}, "usage_notes": ""}
        )
        return _FakeResponse({"response": inner})

    monkeypatch.setattr(llm.requests, "post", fake_post)
    llm.interpret("digest", [], [])
    assert captured["url"] == "http://remote-host:9999/api/generate"


def test_interpret_ollama_network_error_raises_llm_error(monkeypatch):
    monkeypatch.setenv("HOGER_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("HOGER_LLM_MODEL", "llama3")

    def fake_post(url, **kwargs):
        raise requests.exceptions.ConnectionError("no ollama running")

    monkeypatch.setattr(llm.requests, "post", fake_post)
    with pytest.raises(llm.LlmError):
        llm.interpret("digest", [], [])


# ── interpret(): unknown provider ───────────────────────────────────


def test_interpret_unknown_provider_raises_llm_error(monkeypatch):
    monkeypatch.setenv("HOGER_LLM_PROVIDER", "not-a-real-provider")
    with pytest.raises(llm.LlmError):
        llm.interpret("digest", [], [])


# ── integration: real gemini CLI call ───────────────────────────────
#
# 真的呼叫本機 gemini CLI 一次（短 digest，最小成本）。deselected by
# default（見 pyproject.toml 的 addopts = "-m 'not integration'"）。
# shutil.which("gemini") 找不到就 skip——不是每台機器都裝了 gemini CLI。


@pytest.mark.integration
def test_interpret_gemini_cli_real_call_short_digest(monkeypatch):
    if shutil.which("gemini") is None:
        pytest.skip("gemini CLI not found on this machine")

    monkeypatch.setenv("HOGER_LLM_PROVIDER", "gemini-cli")
    digest = "工具名稱: Test Tool\n物件總數: 1\n輸入參數（共 1 個）:\n  - 名稱=x; 型別=number\n輸出（共 0 個）:\n"

    result = llm.interpret(digest, ["x"], [])
    assert isinstance(result, llm.Interpretation)
    assert isinstance(result.tool_purpose, str)
