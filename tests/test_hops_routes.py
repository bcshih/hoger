"""
tests/test_hops_routes.py — hoger.hops.hops_routes（Hops 端點）測試。

Grasshopper 的 Hops 元件指向一個 URL 時：
1. GET  {url}       -> /io 相容的 JSON，Hops 據此生成元件參數
2. POST {url}/solve -> body {"pointer": ..., "values": [...]}
                    -> Grasshopper 風格回應 {"values": [...]}

關鍵規則（生產驗證過）：Hops solve 送來的 InnerTree items 必須原樣
passthrough 給 Rhino.Compute，不 decode/re-encode（rhino3dm 往返會損壞
部分 Brep）。本測試用 monkeypatch 攔截 compute_client.evaluate，斷言
它收到的 tree_payloads 與送入的 values 完全相同（同一物件、未被改寫）。

用 fastapi.testclient.TestClient 直接打 app，monkeypatch：
- hoger.config.TOOLS_DIR（指到 tmp_path，隔離測試）
- hoger.core.executor.compute_client.evaluate（避免真的打 Rhino.Compute）
"""

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from hoger.api.app import app
from hoger.core.compute_client import ComputeError
from hoger.core.manifest import InputSpec, OutputSpec, ToolManifest
from hoger.store import tool_store


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def isolated_tools_dir(tmp_path, monkeypatch):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    monkeypatch.setattr("hoger.config.TOOLS_DIR", tools_dir)
    monkeypatch.setattr("hoger.api.routes.TOOLS_DIR", tools_dir)
    monkeypatch.setattr("hoger.hops.hops_routes.TOOLS_DIR", tools_dir)
    return tools_dir


def _make_manifest(**kwargs) -> ToolManifest:
    now = datetime.now(timezone.utc).isoformat()
    return ToolManifest(
        id=kwargs.get("id", "radiation-study"),
        display_name=kwargs.get("display_name", "Radiation Study"),
        description=kwargs.get("description", ""),
        gh_file=kwargs.get("gh_file", "radiation study.gh"),
        status=kwargs.get("status", "registered"),
        inputs=kwargs.get("inputs", []),
        outputs=kwargs.get("outputs", []),
        created_at=kwargs.get("created_at", now),
        updated_at=kwargs.get("updated_at", now),
    )


def _save(tools_dir, manifest: ToolManifest):
    tool_store.save(manifest, tools_dir=tools_dir)


def _full_inputs():
    return [
        InputSpec(
            param_name="_geometry",
            label="",
            kind="geometry",
            param_type="Brep",
            description="要分析的幾何面",
            required=True,
            at_least=1,
            at_most=2147483647,
        ),
        InputSpec(
            param_name="context_",
            label="",
            kind="geometry",
            param_type="Brep",
            description="遮蔽物（選填）",
            required=False,
            at_least=0,
            at_most=2147483647,
        ),
        InputSpec(
            param_name="_grid_size",
            label="Grid Size",
            kind="number",
            param_type="Number",
            description="網格大小（公尺）",
            required=False,
            default=1.0,
            minimum=0.1,
            maximum=50.0,
            at_least=1,
            at_most=1,
        ),
        InputSpec(
            param_name="_run",
            label="",
            kind="boolean",
            param_type="Boolean",
            description="執行開關",
            required=False,
            default=False,
            at_least=1,
            at_most=1,
        ),
    ]


def _full_outputs():
    return [
        OutputSpec(param_name="Mesh", kind="geometry", description="", unit=""),
        OutputSpec(param_name="total", kind="number", description="總量", unit=""),
    ]


# ── GET /hops/{tool_id} ─────────────────────────────────────────────────


def test_get_hops_definition_registered_tool(client, isolated_tools_dir):
    manifest = _make_manifest(
        id="radiation-study",
        description="radiation study",
        inputs=_full_inputs(),
        outputs=_full_outputs(),
        status="registered",
    )
    _save(isolated_tools_dir, manifest)

    resp = client.get("/hops/radiation-study")
    assert resp.status_code == 200
    data = resp.json()

    assert data["Description"] == "radiation study"
    assert data["InputNames"] == ["_geometry", "context_", "_grid_size", "_run"]
    assert data["OutputNames"] == ["Mesh", "total"]
    assert len(data["Inputs"]) == 4
    assert len(data["Outputs"]) == 2

    geo_input = data["Inputs"][0]
    assert geo_input["Name"] == "_geometry"
    assert geo_input["Nickname"] == "_geometry"
    assert geo_input["Description"] == "要分析的幾何面"
    # manifest 存有原始 param_type "Brep" 時原樣使用；kind 反推只在
    # param_type 缺值時發生（見 test_get_hops_definition_kind_to_paramtype_inference）
    assert geo_input["ParamType"] == "Brep"
    assert geo_input["AtLeast"] == 1
    assert geo_input["AtMost"] == 2147483647
    assert "Default" not in geo_input
    assert "Minimum" not in geo_input
    assert "Maximum" not in geo_input

    context_input = data["Inputs"][1]
    assert context_input["AtLeast"] == 0
    assert context_input["AtMost"] == 2147483647

    grid_input = data["Inputs"][2]
    assert grid_input["Name"] == "_grid_size"
    assert grid_input["Nickname"] == "Grid Size"
    assert grid_input["ParamType"] == "Number"
    assert grid_input["Default"] == 1.0
    assert grid_input["Minimum"] == 0.1
    assert grid_input["Maximum"] == 50.0
    assert grid_input["AtMost"] == 1

    run_input = data["Inputs"][3]
    assert run_input["ParamType"] == "Boolean"
    assert run_input["Default"] is False
    assert "Minimum" not in run_input
    assert "Maximum" not in run_input

    mesh_output = data["Outputs"][0]
    assert mesh_output["Name"] == "Mesh"
    assert mesh_output["Nickname"] == "Mesh"
    assert mesh_output["ParamType"] == "Geometry"

    total_output = data["Outputs"][1]
    assert total_output["Name"] == "total"
    assert total_output["ParamType"] == "Number"
    assert total_output["Description"] == "總量"


def test_get_hops_definition_at_most_none_becomes_max_int(client, isolated_tools_dir):
    manifest = _make_manifest(
        inputs=[
            InputSpec(
                param_name="width",
                kind="number",
                required=True,
                at_least=1,
                at_most=None,
            )
        ],
        outputs=[],
    )
    _save(isolated_tools_dir, manifest)

    resp = client.get(f"/hops/{manifest.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["Inputs"][0]["AtMost"] == 2147483647


def test_get_hops_definition_description_falls_back_to_display_name(client, isolated_tools_dir):
    manifest = _make_manifest(id="no-desc-tool", display_name="No Desc Tool", description="")
    _save(isolated_tools_dir, manifest)

    resp = client.get("/hops/no-desc-tool")
    assert resp.status_code == 200
    assert resp.json()["Description"] == "No Desc Tool"


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("number", "Number"),
        ("integer", "Integer"),
        ("boolean", "Boolean"),
        ("string", "String"),
        ("geometry", "Geometry"),
    ],
)
def test_get_hops_definition_kind_to_paramtype_inference(client, isolated_tools_dir, kind, expected):
    manifest = _make_manifest(
        id=f"kind-{kind}",
        inputs=[InputSpec(param_name="p", kind=kind, param_type="", required=False)],
        outputs=[OutputSpec(param_name="o", kind=kind)],
    )
    _save(isolated_tools_dir, manifest)

    resp = client.get(f"/hops/kind-{kind}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["Inputs"][0]["ParamType"] == expected
    assert data["Outputs"][0]["ParamType"] == expected


def test_get_hops_definition_draft_tool_returns_404(client, isolated_tools_dir):
    manifest = _make_manifest(id="draft-tool", status="draft")
    _save(isolated_tools_dir, manifest)

    resp = client.get("/hops/draft-tool")
    assert resp.status_code == 404


def test_get_hops_definition_nonexistent_tool_returns_404(client, isolated_tools_dir):
    resp = client.get("/hops/does-not-exist")
    assert resp.status_code == 404


# ── POST /hops/{tool_id}/solve ───────────────────────────────────────────


def _values_payload():
    return [
        {
            "ParamName": "_grid_size",
            "InnerTree": {"{0}": [{"type": "System.Double", "data": "2.0"}]},
        }
    ]


def test_post_solve_passthrough_values_to_evaluate(client, isolated_tools_dir, monkeypatch):
    manifest = _make_manifest(
        inputs=[
            InputSpec(param_name="_grid_size", kind="number", required=False, default=1.0)
        ],
        outputs=[OutputSpec(param_name="total", kind="number")],
        status="registered",
    )
    _save(isolated_tools_dir, manifest)

    sent_values = _values_payload()
    fake_response = {
        "values": [
            {
                "ParamName": "RH_OUT:total",
                "InnerTree": {"{0}": [{"type": "System.Double", "data": "42.0"}]},
            }
        ]
    }

    captured = {}

    def fake_evaluate(gh_path, tree_payloads):
        captured["payloads"] = tree_payloads
        return fake_response

    monkeypatch.setattr("hoger.core.executor.compute_client.evaluate", fake_evaluate)

    resp = client.post(
        f"/hops/{manifest.id}/solve",
        json={"pointer": "somepointer", "values": sent_values},
    )
    assert resp.status_code == 200

    # passthrough：executor 傳給 compute_client.evaluate 的 payload 與送入的
    # values 完全相同結構（未被 decode/re-encode 改寫）
    assert captured["payloads"] == sent_values

    data = resp.json()
    assert data["values"] == fake_response["values"]
    assert data["errors"] == []
    assert data["warnings"] == []


def test_post_solve_missing_values_returns_400(client, isolated_tools_dir):
    manifest = _make_manifest()
    _save(isolated_tools_dir, manifest)

    resp = client.post(f"/hops/{manifest.id}/solve", json={"pointer": "x"})
    assert resp.status_code == 400


def test_post_solve_values_not_a_list_returns_400(client, isolated_tools_dir):
    manifest = _make_manifest()
    _save(isolated_tools_dir, manifest)

    resp = client.post(
        f"/hops/{manifest.id}/solve", json={"pointer": "x", "values": "not-a-list"}
    )
    assert resp.status_code == 400


def test_post_solve_draft_tool_returns_404(client, isolated_tools_dir):
    manifest = _make_manifest(id="draft-solve", status="draft")
    _save(isolated_tools_dir, manifest)

    resp = client.post(f"/hops/draft-solve/solve", json={"values": []})
    assert resp.status_code == 404


def test_post_solve_nonexistent_tool_returns_404(client, isolated_tools_dir):
    resp = client.post("/hops/does-not-exist/solve", json={"values": []})
    assert resp.status_code == 404


def test_post_solve_compute_error_returns_502(client, isolated_tools_dir, monkeypatch):
    manifest = _make_manifest(status="registered")
    _save(isolated_tools_dir, manifest)

    def raise_compute_error(gh_path, tree_payloads):
        raise ComputeError("Rhino.Compute HTTP 500: boom", status_code=500, body="boom")

    monkeypatch.setattr("hoger.core.executor.compute_client.evaluate", raise_compute_error)

    resp = client.post(f"/hops/{manifest.id}/solve", json={"values": []})
    assert resp.status_code == 502
    assert "errors" in resp.json()
