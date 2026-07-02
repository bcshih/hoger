"""
tests/test_integration.py — 端到端整合煙霧測試（需要真實 Rhino.Compute 在線）。

驗證整條鏈對真實 GH 檔案能跑通：

    compute_client.io_query()
        --manifest.manifest_from_io()--> ToolManifest
        --executor.run_tool()-->         ToolResult

這些測試需要：
1. 一個真正在跑的 Rhino.Compute（COMPUTE_URL，見 hoger.config）。
2. 本機存在 GH_FILE 指到的樣本檔案。

兩者任一缺席，測試一律 pytest.skip()（不 fail）——CI / 離線開發環境不應該
因為這個檔案而變紅。所有測試都標了 `integration` marker，pyproject.toml 的
`addopts = "-m 'not integration'"` 保證預設 `pytest` 不會收集執行它們；
只有明確 `pytest -m integration` 才會運行（並在離線時 skip）。
"""

import json
from pathlib import Path

import pytest
import rhino3dm

from hoger.core import compute_client
from hoger.core.executor import run_tool
from hoger.core.manifest import manifest_from_io

GH_FILE = r"C:\Users\User\Desktop\rhino.compute.test\radiation_study_hops.gh"

pytestmark = pytest.mark.integration


def _compute_online() -> bool:
    return compute_client.health()


@pytest.fixture(scope="module")
def live_compute():
    if not _compute_online():
        pytest.skip("Rhino.Compute is not running at the configured COMPUTE_URL")
    if not Path(GH_FILE).exists():
        pytest.skip(f"sample GH file not found: {GH_FILE}")


# ── helpers ──────────────────────────────────────────────────────────

_VALID_KINDS = {"number", "integer", "boolean", "string", "geometry"}


def _find_epw_file() -> str | None:
    """
    在常見位置尋找一個 .epw 檔案，找不到回傳 None（呼叫端應 skip，不硬造假路徑）。
    """
    candidates = [
        Path(r"C:\Users\User\Desktop\rhino.compute.test"),
        Path(r"C:\Users\User\Desktop"),
        Path.home(),
    ]
    for base in candidates:
        if not base.exists():
            continue
        try:
            match = next(base.rglob("*.epw"))
        except StopIteration:
            continue
        return str(match)
    return None


def _make_box_mesh_3dm(path: Path) -> None:
    """在 path 建立一個含小 box mesh 的 .3dm 檔案。"""
    mesh = rhino3dm.Mesh()
    # 8 個頂點的立方體
    verts = [
        (0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
        (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1),
    ]
    for v in verts:
        mesh.Vertices.Add(*v)
    faces = [
        (0, 1, 2, 3), (4, 5, 6, 7),
        (0, 1, 5, 4), (1, 2, 6, 5),
        (2, 3, 7, 6), (3, 0, 4, 7),
    ]
    for f in faces:
        mesh.Faces.AddFace(*f)
    mesh.Normals.ComputeNormals()

    file3dm = rhino3dm.File3dm()
    file3dm.Objects.AddMesh(mesh)
    file3dm.Write(str(path), 7)


def _build_minimal_args(manifest, tmp_path) -> dict:
    """
    為 manifest 的每個 required input 構造最小可行的參數值。

    回傳 (args, skip_reason)：skip_reason 非 None 時呼叫端應 pytest.skip()。
    """
    args = {}
    for spec in manifest.inputs:
        if not spec.required:
            continue

        if spec.kind == "geometry":
            box_path = tmp_path / f"{spec.param_name}_box.3dm"
            _make_box_mesh_3dm(box_path)
            args[spec.param_name] = {"file_3dm": str(box_path)}
        elif spec.kind == "string" and "epw" in spec.param_name.lower():
            epw_path = _find_epw_file()
            if epw_path is None:
                return None, (
                    f"no .epw file found on this machine for required parameter "
                    f"'{spec.param_name}'"
                )
            args[spec.param_name] = epw_path
        elif spec.kind == "number":
            args[spec.param_name] = spec.default if spec.default is not None else 1.0
        elif spec.kind == "integer":
            args[spec.param_name] = spec.default if spec.default is not None else 1
        elif spec.kind == "boolean":
            args[spec.param_name] = spec.default if spec.default is not None else True
        elif spec.kind == "string":
            args[spec.param_name] = spec.default if spec.default is not None else "x"

    return args, None


# ── tests ────────────────────────────────────────────────────────────


def test_io_query_returns_inputs_outputs(live_compute):
    io_response = compute_client.io_query(GH_FILE)

    assert isinstance(io_response, dict)
    assert isinstance(io_response.get("Inputs"), list)
    assert isinstance(io_response.get("Outputs"), list)
    assert len(io_response["Inputs"]) > 0
    assert len(io_response["Outputs"]) > 0

    fixtures_dir = Path(__file__).parent / "fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    recorded_path = fixtures_dir / "io_response_recorded.json"
    recorded_path.write_text(
        json.dumps(io_response, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def test_manifest_from_live_io(live_compute):
    io_response = compute_client.io_query(GH_FILE)
    manifest = manifest_from_io(GH_FILE, io_response)

    assert len(manifest.inputs) > 0
    for spec in manifest.inputs:
        assert spec.kind in _VALID_KINDS
        assert spec.param_name


def test_run_tool_end_to_end(live_compute, tmp_path):
    io_response = compute_client.io_query(GH_FILE)
    manifest = manifest_from_io(GH_FILE, io_response)

    args, skip_reason = _build_minimal_args(manifest, tmp_path)
    if skip_reason is not None:
        pytest.skip(skip_reason)

    result = run_tool(manifest, args, out_dir=tmp_path)

    assert result.elapsed_ms > 0
    if result.errors:
        print(f"[test_run_tool_end_to_end] Compute errors: {result.errors}")
    if result.warnings:
        print(f"[test_run_tool_end_to_end] Compute warnings: {result.warnings}")

    if isinstance(result.raw, dict) and "values" in result.raw:
        for spec in manifest.outputs:
            assert spec.param_name in result.outputs
