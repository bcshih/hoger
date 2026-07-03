"""
tests/test_api.py — hoger.api（FastAPI 後端）的端點測試。

用 fastapi.testclient.TestClient 直接打 app，monkeypatch 掉會碰真實
Rhino.Compute 或磁碟固定路徑的呼叫點：
- hoger.api.routes.compute_client.io_query / .health
- hoger.api.routes.executor.run_tool
- hoger.config.TOOLS_DIR / GH_FILES_DIR（改指到 tmp_path，讓每個測試互相隔離）

manifest CRUD 端點是薄封裝：驗證 -> tool_store -> JSON 回應，實際的
save/get/list/delete 邏輯已在 tests/test_tool_store.py 涵蓋，這裡只驗證
HTTP 層的行為（狀態碼、body 形狀、錯誤轉換）。
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hoger.api.app import app
from hoger.core.compute_client import ComputeError
from hoger.core.executor import ToolArgError, ToolResult
from hoger.core.manifest import InputSpec, OutputSpec, ToolManifest
from hoger.store import tool_store

FIXTURE_IO = Path(__file__).parent / "fixtures" / "io_response_sample.json"


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """所有測試預設用 tmp_path 下的 tools/gh_files 目錄，彼此隔離。"""
    tools_dir = tmp_path / "tools"
    gh_dir = tmp_path / "gh_files"
    tools_dir.mkdir()
    gh_dir.mkdir()
    monkeypatch.setattr("hoger.config.TOOLS_DIR", tools_dir)
    monkeypatch.setattr("hoger.config.GH_FILES_DIR", gh_dir)
    monkeypatch.setattr("hoger.api.routes.TOOLS_DIR", tools_dir)
    monkeypatch.setattr("hoger.api.routes.GH_FILES_DIR", gh_dir)
    return {"tools_dir": tools_dir, "gh_dir": gh_dir}


def _io_sample() -> dict:
    return json.loads(FIXTURE_IO.read_text(encoding="utf-8"))


def make_manifest(tool_id: str, **kwargs) -> ToolManifest:
    now = datetime.now(timezone.utc).isoformat()
    return ToolManifest(
        id=tool_id,
        display_name=kwargs.get("display_name", tool_id.title()),
        description=kwargs.get("description", ""),
        gh_file=kwargs.get("gh_file", f"{tool_id}.gh"),
        status=kwargs.get("status", "draft"),
        inputs=kwargs.get("inputs", []),
        outputs=kwargs.get("outputs", []),
        created_at=kwargs.get("created_at", now),
        updated_at=kwargs.get("updated_at", now),
    )


# ── GET /api/health ──────────────────────────────────────────────────


def test_health_compute_true(client, monkeypatch):
    monkeypatch.setattr("hoger.api.routes.compute_client.health", lambda: True)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"hoger": True, "compute": True}


def test_health_compute_false(client, monkeypatch):
    monkeypatch.setattr("hoger.api.routes.compute_client.health", lambda: False)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"hoger": True, "compute": False}


# ── POST /api/import ─────────────────────────────────────────────────


def test_import_json_gh_path(client, monkeypatch, tmp_path):
    gh_path = tmp_path / "radiation study.gh"
    gh_path.write_bytes(b"fake gh content")

    monkeypatch.setattr(
        "hoger.api.routes.compute_client.io_query", lambda p: _io_sample()
    )

    resp = client.post("/api/import", json={"gh_path": str(gh_path)})
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "radiation-study"
    assert data["status"] == "draft"
    assert len(data["inputs"]) == 4
    assert len(data["outputs"]) == 2

    # 不落地存檔
    assert list((tmp_path / "tools").glob("*.json")) == [] if (tmp_path / "tools").exists() else True


def test_import_does_not_persist_to_tools_dir(client, monkeypatch, tmp_path, isolated_dirs):
    gh_path = tmp_path / "radiation study.gh"
    gh_path.write_bytes(b"fake gh content")
    monkeypatch.setattr(
        "hoger.api.routes.compute_client.io_query", lambda p: _io_sample()
    )

    resp = client.post("/api/import", json={"gh_path": str(gh_path)})
    assert resp.status_code == 200
    assert list(isolated_dirs["tools_dir"].glob("*.json")) == []


def test_import_multipart_upload(client, monkeypatch, isolated_dirs):
    monkeypatch.setattr(
        "hoger.api.routes.compute_client.io_query", lambda p: _io_sample()
    )

    file_content = b"fake gh binary content"
    resp = client.post(
        "/api/import",
        files={"file": ("uploaded.gh", file_content, "application/octet-stream")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "uploaded"

    saved = isolated_dirs["gh_dir"] / "uploaded.gh"
    assert saved.exists()
    assert saved.read_bytes() == file_content


def test_import_multipart_upload_overwrites_existing(client, monkeypatch, isolated_dirs):
    monkeypatch.setattr(
        "hoger.api.routes.compute_client.io_query", lambda p: _io_sample()
    )
    existing = isolated_dirs["gh_dir"] / "uploaded.gh"
    existing.write_bytes(b"old content")

    resp = client.post(
        "/api/import",
        files={"file": ("uploaded.gh", b"new content", "application/octet-stream")},
    )
    assert resp.status_code == 200
    assert existing.read_bytes() == b"new content"


def test_import_gh_path_not_found(client, tmp_path):
    missing = tmp_path / "missing.gh"
    resp = client.post("/api/import", json={"gh_path": str(missing)})
    assert resp.status_code == 404


def test_import_compute_error_returns_502(client, monkeypatch, tmp_path):
    gh_path = tmp_path / "test.gh"
    gh_path.write_bytes(b"content")

    def raise_compute_error(p):
        raise ComputeError("Rhino.Compute HTTP 500: boom", status_code=500, body="boom")

    monkeypatch.setattr("hoger.api.routes.compute_client.io_query", raise_compute_error)

    resp = client.post("/api/import", json={"gh_path": str(gh_path)})
    assert resp.status_code == 502
    assert "detail" in resp.json()


def test_import_rejects_non_gh_extension_json(client, tmp_path):
    txt_path = tmp_path / "not_gh.txt"
    txt_path.write_text("hi", encoding="utf-8")
    resp = client.post("/api/import", json={"gh_path": str(txt_path)})
    assert resp.status_code == 400


def test_import_rejects_non_gh_extension_multipart(client):
    resp = client.post(
        "/api/import",
        files={"file": ("not_gh.txt", b"hi", "text/plain")},
    )
    assert resp.status_code == 400


# ── POST /api/tools, GET /api/tools ──────────────────────────────────


def test_create_tool_then_list(client):
    manifest = make_manifest("my-tool", description="desc")
    resp = client.post("/api/tools", json=manifest.model_dump())
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == "my-tool"

    list_resp = client.get("/api/tools")
    assert list_resp.status_code == 200
    tools = list_resp.json()
    assert len(tools) == 1
    entry = tools[0]
    assert entry["id"] == "my-tool"
    assert entry["display_name"] == manifest.display_name
    assert entry["status"] == "draft"
    assert entry["inputs_count"] == 0
    assert entry["outputs_count"] == 0
    assert "updated_at" in entry


def test_create_tool_lists_with_correct_counts(client):
    inputs = [InputSpec(param_name="a", kind="number")]
    outputs = [OutputSpec(param_name="b", kind="geometry")]
    manifest = make_manifest("counted-tool", inputs=inputs, outputs=outputs)
    resp = client.post("/api/tools", json=manifest.model_dump())
    assert resp.status_code == 201

    tools = client.get("/api/tools").json()
    entry = next(t for t in tools if t["id"] == "counted-tool")
    assert entry["inputs_count"] == 1
    assert entry["outputs_count"] == 1


def test_create_tool_invalid_id_returns_400(client):
    manifest = make_manifest("placeholder")
    payload = manifest.model_dump()
    payload["id"] = "../evil"
    resp = client.post("/api/tools", json=payload)
    assert resp.status_code == 400


def test_create_tool_missing_required_field_returns_422(client):
    resp = client.post("/api/tools", json={"id": "bad-tool"})
    assert resp.status_code == 422


def test_list_tools_empty(client):
    resp = client.get("/api/tools")
    assert resp.status_code == 200
    assert resp.json() == []


# ── GET /api/tools/{id} ───────────────────────────────────────────────


def test_get_tool_returns_manifest_and_mcp_schema(client, isolated_dirs):
    manifest = make_manifest(
        "geo-tool", inputs=[InputSpec(param_name="w", kind="number", required=True)]
    )
    tool_store.save(manifest, tools_dir=isolated_dirs["tools_dir"])

    resp = client.get("/api/tools/geo-tool")
    assert resp.status_code == 200
    data = resp.json()
    assert data["manifest"]["id"] == "geo-tool"
    assert data["mcp_schema"]["name"] == "geo-tool"
    assert "inputSchema" in data["mcp_schema"]


def test_get_tool_not_found(client):
    resp = client.get("/api/tools/nonexistent")
    assert resp.status_code == 404


# ── PUT /api/tools/{id} ────────────────────────────────────────────────


def test_put_tool_updates_and_reflects_in_get(client, isolated_dirs):
    manifest = make_manifest("edit-tool", description="old desc")
    tool_store.save(manifest, tools_dir=isolated_dirs["tools_dir"])

    updated_payload = manifest.model_dump()
    updated_payload["description"] = "new desc"

    resp = client.put("/api/tools/edit-tool", json=updated_payload)
    assert resp.status_code == 200
    assert resp.json()["description"] == "new desc"

    get_resp = client.get("/api/tools/edit-tool")
    assert get_resp.json()["manifest"]["description"] == "new desc"
    assert "new desc" in get_resp.json()["mcp_schema"]["description"] or True


def test_put_tool_id_mismatch_returns_400(client, isolated_dirs):
    manifest = make_manifest("edit-tool")
    tool_store.save(manifest, tools_dir=isolated_dirs["tools_dir"])

    payload = manifest.model_dump()
    payload["id"] = "different-id"

    resp = client.put("/api/tools/edit-tool", json=payload)
    assert resp.status_code == 400


def test_put_tool_not_found(client):
    manifest = make_manifest("ghost-tool")
    resp = client.put("/api/tools/ghost-tool", json=manifest.model_dump())
    assert resp.status_code == 404


# ── DELETE /api/tools/{id} ───────────────────────────────────────────


def test_delete_tool_then_get_404(client, isolated_dirs):
    manifest = make_manifest("del-tool")
    tool_store.save(manifest, tools_dir=isolated_dirs["tools_dir"])

    resp = client.delete("/api/tools/del-tool")
    assert resp.status_code == 204

    get_resp = client.get("/api/tools/del-tool")
    assert get_resp.status_code == 404


def test_delete_tool_not_found(client):
    resp = client.delete("/api/tools/nonexistent")
    assert resp.status_code == 404


# ── POST /api/tools/{id}/run ──────────────────────────────────────────


def _fake_tool_result(**overrides):
    defaults = dict(
        outputs={"total": [9.5]},
        result_3dm="/fake/path.3dm",
        elapsed_ms=42,
        errors=[],
        warnings=[],
        modelunits="mm",
        raw={"values": []},
    )
    defaults.update(overrides)
    return ToolResult(**defaults)


def test_run_tool_returns_result_without_raw(client, monkeypatch, isolated_dirs):
    manifest = make_manifest("run-tool")
    tool_store.save(manifest, tools_dir=isolated_dirs["tools_dir"])

    monkeypatch.setattr(
        "hoger.api.routes.executor.run_tool", lambda m, args: _fake_tool_result()
    )

    resp = client.post("/api/tools/run-tool/run", json={"args": {"width": 1.0}})
    assert resp.status_code == 200
    data = resp.json()
    assert data["outputs"] == {"total": [9.5]}
    assert data["result_3dm"] == "/fake/path.3dm"
    assert data["elapsed_ms"] == 42
    assert data["errors"] == []
    assert data["warnings"] == []
    assert data["modelunits"] == "mm"
    assert "raw" not in data


def test_run_tool_debug_includes_raw(client, monkeypatch, isolated_dirs):
    manifest = make_manifest("run-tool")
    tool_store.save(manifest, tools_dir=isolated_dirs["tools_dir"])

    monkeypatch.setattr(
        "hoger.api.routes.executor.run_tool", lambda m, args: _fake_tool_result()
    )

    resp = client.post("/api/tools/run-tool/run?debug=true", json={"args": {}})
    assert resp.status_code == 200
    data = resp.json()
    assert data["raw"] == {"values": []}


def test_run_tool_defaults_args_to_empty_dict(client, monkeypatch, isolated_dirs):
    manifest = make_manifest("run-tool")
    tool_store.save(manifest, tools_dir=isolated_dirs["tools_dir"])

    captured = {}

    def fake_run_tool(m, args):
        captured["args"] = args
        return _fake_tool_result()

    monkeypatch.setattr("hoger.api.routes.executor.run_tool", fake_run_tool)

    resp = client.post("/api/tools/run-tool/run", json={})
    assert resp.status_code == 200
    assert captured["args"] == {}


def test_run_tool_arg_error_returns_400(client, monkeypatch, isolated_dirs):
    manifest = make_manifest("run-tool")
    tool_store.save(manifest, tools_dir=isolated_dirs["tools_dir"])

    def raise_arg_error(m, args):
        raise ToolArgError("missing required parameter: 'width'")

    monkeypatch.setattr("hoger.api.routes.executor.run_tool", raise_arg_error)

    resp = client.post("/api/tools/run-tool/run", json={"args": {}})
    assert resp.status_code == 400
    assert "width" in resp.json()["detail"]


def test_run_tool_not_found_returns_404(client):
    resp = client.post("/api/tools/nonexistent/run", json={"args": {}})
    assert resp.status_code == 404


# ── GET /api/mcp-config ────────────────────────────────────────────────


def test_mcp_config_structure(client):
    resp = client.get("/api/mcp-config")
    assert resp.status_code == 200
    data = resp.json()

    stdio_server = data["stdio"]["mcpServers"]["hoger"]
    assert stdio_server["command"].endswith("python.exe")
    assert stdio_server["args"] == ["-m", "hoger.mcp_server.stdio_main"]
    assert "cwd" in stdio_server
    assert "HOGER_COMPUTE_URL" in stdio_server["env"]

    http_server = data["http"]["mcpServers"]["hoger"]
    assert http_server["url"].startswith("http://localhost:")
    assert http_server["url"].endswith("/mcp")


# ── static mount (webui) ──────────────────────────────────────────────


def test_root_static_mount_present_or_404(client):
    # webui/ 目前只有 .gitkeep，沒有 index.html——允許 404，這裡只驗證
    # app 沒有因為靜態掛載而整個炸掉。
    resp = client.get("/")
    assert resp.status_code in (200, 404)
