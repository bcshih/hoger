"""
tests/test_mcp_transports.py — Task 4.2：MCP stdio 入口 + Streamable HTTP 掛載測試。

分兩層：
(a) 單元測試：不需要真正的 server，檢查 import 無副作用、logging 設定、
    /mcp 掛載順序（必須在 webui 的 "/" StaticFiles 之前）。
(b) 協定級 smoke test：真正在背景 thread 跑 uvicorn，用 mcp SDK 的
    streamable_http_client + ClientSession 對 /mcp 做 initialize + list_tools，
    驗證整條 ASGI mount 路徑真的可用（不只是「掛載存在」）。

沒有 pytest-asyncio 插件（見 tests/test_mcp_server.py 的說明），協定測試
一樣透過 asyncio.run() 包一層 async 函式來跑。
"""

import asyncio
import logging
import socket
import sys
import threading
import time
from datetime import datetime, timezone

import httpx
import pytest
import uvicorn
from starlette.routing import Mount

import hoger.config as hoger_config
from hoger.core.manifest import ToolManifest
from hoger.store import tool_store


def make_manifest(tool_id: str, status: str = "registered", **kwargs) -> ToolManifest:
    """與 tests/test_mcp_server.py 相同的 helper，保持慣例一致。"""
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


# ── (a) unit tests ───────────────────────────────────────────────────


def test_stdio_main_import_has_no_side_effects(capfd):
    """
    import 本身不得啟動 event loop 或碰 stdio——main()/_amain() 只在
    `if __name__ == "__main__":` 下執行，import 這個動作不會觸發它們。
    用 capfd 確認 import 期間沒有任何 stdout/stderr 輸出。
    """
    import hoger.mcp_server.stdio_main as stdio_main

    out, err = capfd.readouterr()
    assert out == ""
    # 模組層級不應該有 logging handler 被加到 root logger（_setup_logging()
    # 尚未被呼叫），也不該有任何例外訊息跑到 stderr。
    assert err == ""

    assert hasattr(stdio_main, "main")
    assert hasattr(stdio_main, "_amain")
    assert callable(stdio_main.main)


def test_setup_logging_targets_stderr():
    """_setup_logging() 設定的 handler.stream 必須是 sys.stderr。"""
    from hoger.mcp_server.stdio_main import _setup_logging

    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    try:
        root.handlers = []
        _setup_logging()
        assert len(root.handlers) >= 1
        assert any(getattr(h, "stream", None) is sys.stderr for h in root.handlers)
    finally:
        root.handlers = saved_handlers
        root.level = saved_level


def test_mcp_mount_before_webui_static_mount():
    """
    /mcp 必須先於 "/" webui StaticFiles 掛載，否則 StaticFiles 會攔截所有
    未匹配路徑（含 /mcp），導致 MCP 端點永遠打不到。
    """
    from hoger.api.app import app

    mounts = [(i, r) for i, r in enumerate(app.routes) if isinstance(r, Mount)]
    paths = {r.path: i for i, r in mounts}

    # Starlette 的 Mount("/") 內部正規化後 .path 是空字串 ""，不是 "/"。
    assert "/mcp" in paths
    assert "" in paths
    assert paths["/mcp"] < paths[""], "/mcp 必須出現在 webui 的 '/' 掛載之前"


# ── (b) protocol smoke test ─────────────────────────────────────────


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_mcp_streamable_http_end_to_end(tmp_path):
    """
    真實 uvicorn + 真實 mcp client：起一個背景 thread 跑 hoger.api.app:app，
    seed 一個 registered 工具進 tmp tools_dir，透過 mcp SDK 的
    streamable_http_client + ClientSession 對 /mcp 做 initialize +
    list_tools，確認能看到剛剛 seed 的工具——證明整條 ASGI mount 路徑
    （FastAPI lifespan -> StreamableHTTPSessionManager.run() -> task group
    -> handle_request）真的可用，不只是「掛載物件存在」。
    """
    tool_id = "smoke-cube-tool"
    manifest = make_manifest(tool_id, status="registered")

    original_tools_dir = hoger_config.TOOLS_DIR
    hoger_config.TOOLS_DIR = tmp_path
    tool_store.save(manifest, tools_dir=tmp_path)

    port = _free_port()
    config = uvicorn.Config(
        "hoger.api.app:app",
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)

    try:
        thread.start()

        # 等待 server ready（最多 ~30 秒；冷啟動 / defender 掃描偶爾讓第一次
        # 啟動超過 10 秒，deadline 放寬避免 flaky）。
        # 注意：不要輪詢 /api/health——它會去打 Rhino.Compute（localhost:5000），
        # 測試環境沒有 Compute 時每次呼叫都要等 requests 重試逾時（>1 秒），
        # 客戶端輪詢會一直 timeout、永遠等不到 ready。改用 uvicorn 自己的
        # server.started 旗標 + 輪詢便宜的 /api/tools（只讀 tools 目錄）。
        base_url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 30
        ready = False
        while time.monotonic() < deadline:
            if not thread.is_alive():
                # 兩種可能原因：(1) _free_port() 探測到的 port 在 uvicorn bind 前被
                # 其他行程搶走（TOCTOU race），uvicorn 啟動失敗；(2) session_manager.run()
                # 被二次進入，拋 RuntimeError（發生於同進程重複啟動 app）。
                pytest.fail("uvicorn thread 在啟動完成前就結束（port 被搶 或 session_manager.run() 二次進入）")
            if server.started:
                try:
                    resp = httpx.get(f"{base_url}/api/tools", timeout=2)
                    if resp.status_code == 200:
                        ready = True
                        break
                except httpx.HTTPError:
                    pass
            time.sleep(0.1)
        assert ready, "uvicorn server 未在時限內就緒"

        async def _smoke():
            from mcp.client.session import ClientSession
            from mcp.client.streamable_http import streamable_http_client

            async with streamable_http_client(f"{base_url}/mcp") as (read, write, _get_sid):
                async with ClientSession(read, write) as session:
                    init_result = await session.initialize()
                    return init_result, await session.list_tools()

        init_result, result = asyncio.run(_smoke())

        assert init_result.serverInfo.name == "hoger"
        names = [t.name for t in result.tools]
        assert tool_id in names
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        assert not thread.is_alive(), "uvicorn thread 未在 5s 內停止"
        hoger_config.TOOLS_DIR = original_tools_dir
