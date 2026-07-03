"""
tests/test_mcp_server.py — hoger.mcp_server.server 的單元測試。

server.py 負責從 tool_store 動態列出/呼叫工具（無快取，磁碟即真相）：
1. handle_list_tools(): 只回傳 status=="registered" 的工具，draft 不出現。
2. handle_call_tool(): 查無工具 / draft 工具 / ToolArgError 一律回傳
   isError=True 的 CallToolResult；成功時回傳單一 TextContent，
   text 是 json.dumps(...)（不含 raw）。

handler 函式定義為模組級 async 函式，測試用 asyncio.run() 直接 await，
不透過 decorator-wrapped 版本（venv 沒有 pytest-asyncio 插件）。

executor.run_tool 是同步阻塞呼叫，用 anyio.to_thread.run_sync 包起來避免
卡住 event loop；測試用 monkeypatch anyio.to_thread.run_sync 驗證真的有
透過它呼叫，而不是直接同步呼叫 run_tool。
"""

import asyncio
import json
import threading
from datetime import datetime, timezone

import pytest

import mcp.types as types
from hoger.core.executor import ToolArgError, ToolResult
from hoger.core.manifest import InputSpec, ToolManifest, to_mcp_tool
from hoger.mcp_server import server as mcp_server_module
from hoger.mcp_server.server import handle_call_tool, handle_list_tools
from hoger.store import tool_store


def make_manifest(tool_id: str, status: str = "registered", **kwargs) -> ToolManifest:
    now = datetime.now(timezone.utc).isoformat()
    return ToolManifest(
        id=tool_id,
        display_name=kwargs.get("display_name", tool_id.replace("-", " ").title()),
        description=kwargs.get("description", ""),
        gh_file=kwargs.get("gh_file", f"{tool_id}.gh"),
        status=status,
        inputs=kwargs.get("inputs", []),
        outputs=kwargs.get("outputs", []),
        created_at=kwargs.get("created_at", now),
        updated_at=kwargs.get("updated_at", now),
    )


@pytest.fixture
def tools_dir(tmp_path, monkeypatch):
    """monkeypatch config.TOOLS_DIR，讓 tool_store 的 tools_dir=None 預設值指向 tmp_path。"""
    monkeypatch.setattr("hoger.config.TOOLS_DIR", tmp_path)
    return tmp_path


def run(coro):
    return asyncio.run(coro)


# ── handle_list_tools ────────────────────────────────────────────────


def test_list_tools_only_returns_registered(tools_dir):
    registered = make_manifest("cube-tool", status="registered")
    draft = make_manifest("draft-tool", status="draft")
    tool_store.save(registered, tools_dir=tools_dir)
    tool_store.save(draft, tools_dir=tools_dir)

    result = run(handle_list_tools())

    assert len(result) == 1
    tool = result[0]
    assert isinstance(tool, types.Tool)
    expected = to_mcp_tool(registered)
    assert tool.name == expected["name"]
    assert tool.description == expected["description"]
    assert tool.inputSchema == expected["inputSchema"]


def test_list_tools_empty_store(tools_dir):
    result = run(handle_list_tools())
    assert result == []


def test_list_tools_draft_never_appears(tools_dir):
    draft = make_manifest("draft-tool", status="draft")
    tool_store.save(draft, tools_dir=tools_dir)

    result = run(handle_list_tools())

    assert result == []


# ── handle_call_tool: success path ──────────────────────────────────


def _fake_tool_result(**overrides):
    defaults = dict(
        outputs={"面積": 12.5},
        result_3dm="C:/out/result.3dm",
        elapsed_ms=42,
        errors=[],
        warnings=["注意事項"],
        modelunits="Meters",
        raw={"debug": "should not leak"},
    )
    defaults.update(overrides)
    return ToolResult(**defaults)


def test_call_tool_success_returns_text_content(tools_dir, monkeypatch):
    manifest = make_manifest("cube-tool", status="registered")
    tool_store.save(manifest, tools_dir=tools_dir)

    captured_args = {}

    def fake_run_tool(m, args, out_dir=None):
        captured_args["manifest"] = m
        captured_args["args"] = args
        return _fake_tool_result()

    monkeypatch.setattr(mcp_server_module.executor, "run_tool", fake_run_tool)

    result = run(handle_call_tool("cube-tool", {"size": 2}))

    assert isinstance(result, list)
    assert len(result) == 1
    content = result[0]
    assert isinstance(content, types.TextContent)
    assert content.type == "text"

    payload = json.loads(content.text)
    assert payload["outputs"] == {"面積": 12.5}
    assert payload["result_3dm"] == "C:/out/result.3dm"
    assert payload["elapsed_ms"] == 42
    assert payload["warnings"] == ["注意事項"]
    assert payload["errors"] == []
    assert payload["modelunits"] == "Meters"
    assert "raw" not in payload

    # 中文輸出未被 escape 成 \uXXXX
    assert "面積" in content.text
    assert "注意事項" in content.text

    assert captured_args["args"] == {"size": 2}


def test_call_tool_arguments_none_becomes_empty_dict(tools_dir, monkeypatch):
    manifest = make_manifest("cube-tool", status="registered")
    tool_store.save(manifest, tools_dir=tools_dir)

    captured_args = {}

    def fake_run_tool(m, args, out_dir=None):
        captured_args["args"] = args
        return _fake_tool_result()

    monkeypatch.setattr(mcp_server_module.executor, "run_tool", fake_run_tool)

    run(handle_call_tool("cube-tool", None))

    assert captured_args["args"] == {}


# ── handle_call_tool: error paths ───────────────────────────────────


def test_call_tool_unknown_tool_is_error(tools_dir):
    result = run(handle_call_tool("does-not-exist", {}))

    assert isinstance(result, types.CallToolResult)
    assert result.isError is True
    assert len(result.content) == 1
    assert isinstance(result.content[0], types.TextContent)


def test_call_tool_draft_tool_is_error(tools_dir):
    draft = make_manifest("draft-tool", status="draft")
    tool_store.save(draft, tools_dir=tools_dir)

    result = run(handle_call_tool("draft-tool", {}))

    assert isinstance(result, types.CallToolResult)
    assert result.isError is True


def test_call_tool_tool_arg_error_returns_message(tools_dir, monkeypatch):
    manifest = make_manifest("cube-tool", status="registered")
    tool_store.save(manifest, tools_dir=tools_dir)

    def fake_run_tool(m, args, out_dir=None):
        raise ToolArgError("missing required parameter: 'size'")

    monkeypatch.setattr(mcp_server_module.executor, "run_tool", fake_run_tool)

    result = run(handle_call_tool("cube-tool", {}))

    assert isinstance(result, types.CallToolResult)
    assert result.isError is True
    assert "missing required parameter: 'size'" in result.content[0].text


# ── handle_call_tool: threading ──────────────────────────────────────


def test_call_tool_runs_run_tool_via_to_thread(tools_dir, monkeypatch):
    manifest = make_manifest("cube-tool", status="registered")
    tool_store.save(manifest, tools_dir=tools_dir)

    calls = []
    real_run_sync = mcp_server_module.anyio.to_thread.run_sync

    async def spy_run_sync(func, *args, **kwargs):
        calls.append((func, args))
        return await real_run_sync(func, *args, **kwargs)

    def fake_run_tool(m, args, out_dir=None):
        return _fake_tool_result()

    monkeypatch.setattr(mcp_server_module.executor, "run_tool", fake_run_tool)
    monkeypatch.setattr(mcp_server_module.anyio.to_thread, "run_sync", spy_run_sync)

    run(handle_call_tool("cube-tool", {}))

    assert len(calls) == 1
