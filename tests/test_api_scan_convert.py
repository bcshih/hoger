"""
tests/test_api_scan_convert.py — POST /api/scan、POST /api/convert 的端點測試。

沿用 tests/test_api.py 的模式（TestClient、isolated_dirs autouse fixture）。
monkeypatch 對象一律指向 hoger.api.routes 底下 import 進來的名字（loader /
scanner / marker / compute_client 皆以模組方式 import，見 routes.py）。
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hoger.api.app import app
from hoger.core.compute_client import ComputeError
from hoger.ghio.marker import MarkError, MarkResult
from hoger.ghio.scanner import InputCandidate, OutputCandidate, ScanResult

FIXTURE_IO = Path(__file__).parent / "fixtures" / "io_response_sample.json"


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
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


def _available(monkeypatch, value=True):
    monkeypatch.setattr("hoger.api.routes.loader.is_available", lambda: value)


# ── POST /api/scan ───────────────────────────────────────────────────


def test_scan_json_gh_path_suggested_names(client, monkeypatch, tmp_path):
    _available(monkeypatch)
    gh_path = tmp_path / "model.gh"
    gh_path.write_bytes(b"fake gh content")

    scan_result = ScanResult(
        inputs=[
            InputCandidate(
                instance_guid="11111111-1111-1111-1111-111111111111",
                object_type="Number Slider",
                nickname="Slider",
                current_value="3.0",
                minimum=0.0,
                maximum=10.0,
                feeds=[{"component": "Grid", "input": "_grid_size"}],
            ),
            InputCandidate(
                instance_guid="22222222-2222-2222-2222-222222222222",
                object_type="Boolean Toggle",
                nickname="開關",  # 中文 nickname，無 feeds
                current_value="True",
                minimum=None,
                maximum=None,
                feeds=[],
            ),
        ],
        outputs=[
            OutputCandidate(
                instance_guid="33333333-3333-3333-3333-333333333333",
                object_type="Panel",
                nickname="Result",
                fed_by=[{"component": "Comp", "output": "R"}],
            ),
        ],
        already_marked_count=0,
        object_count=3,
    )
    monkeypatch.setattr("hoger.api.routes.scanner.scan_gh", lambda p: scan_result)

    resp = client.post("/api/scan", json={"gh_path": str(gh_path)})
    assert resp.status_code == 200
    data = resp.json()

    assert data["gh_path"] == str(gh_path)
    assert len(data["scan"]["inputs"]) == 2
    assert len(data["scan"]["outputs"]) == 1

    suggested = data["suggested_names"]
    assert suggested["11111111-1111-1111-1111-111111111111"] == "_grid_size"
    # 中文 nickname 消毒後為空 -> fallback object_type 形式
    assert suggested["22222222-2222-2222-2222-222222222222"] == "boolean_toggle_1"


def test_scan_suggested_names_conflict_suffix(client, monkeypatch, tmp_path):
    _available(monkeypatch)
    gh_path = tmp_path / "model.gh"
    gh_path.write_bytes(b"fake gh content")

    scan_result = ScanResult(
        inputs=[
            InputCandidate(
                instance_guid="11111111-1111-1111-1111-111111111111",
                object_type="Number Slider",
                nickname="Slider A",
                current_value="1.0",
                minimum=0.0,
                maximum=10.0,
                feeds=[{"component": "Grid", "input": "size"}],
            ),
            InputCandidate(
                instance_guid="22222222-2222-2222-2222-222222222222",
                object_type="Number Slider",
                nickname="Slider B",
                current_value="2.0",
                minimum=0.0,
                maximum=10.0,
                feeds=[{"component": "Grid2", "input": "size"}],
            ),
        ],
        outputs=[],
        already_marked_count=0,
        object_count=2,
    )
    monkeypatch.setattr("hoger.api.routes.scanner.scan_gh", lambda p: scan_result)

    resp = client.post("/api/scan", json={"gh_path": str(gh_path)})
    assert resp.status_code == 200
    suggested = resp.json()["suggested_names"]
    names = sorted(suggested.values())
    assert names == ["size", "size_2"]


def test_scan_multipart_upload_saves_and_scans(client, monkeypatch, isolated_dirs):
    _available(monkeypatch)
    scan_result = ScanResult(inputs=[], outputs=[], already_marked_count=0, object_count=0)
    captured_paths = []

    def fake_scan_gh(p):
        captured_paths.append(str(p))
        return scan_result

    monkeypatch.setattr("hoger.api.routes.scanner.scan_gh", fake_scan_gh)

    resp = client.post(
        "/api/scan",
        files={"file": ("uploaded.gh", b"fake content", "application/octet-stream")},
    )
    assert resp.status_code == 200
    saved = isolated_dirs["gh_dir"] / "uploaded.gh"
    assert saved.exists()
    assert saved.read_bytes() == b"fake content"
    assert captured_paths == [str(saved)]


def test_scan_multipart_sanitizes_path_traversal_filename(client, monkeypatch, isolated_dirs, tmp_path):
    _available(monkeypatch)
    scan_result = ScanResult(inputs=[], outputs=[], already_marked_count=0, object_count=0)
    monkeypatch.setattr("hoger.api.routes.scanner.scan_gh", lambda p: scan_result)

    resp = client.post(
        "/api/scan",
        files={"file": ("../evil.gh", b"content", "application/octet-stream")},
    )
    assert resp.status_code == 200

    saved = isolated_dirs["gh_dir"] / "evil.gh"
    assert saved.exists()
    assert saved.read_bytes() == b"content"
    assert not (tmp_path / "evil.gh").exists()


def test_scan_ghio_unavailable_returns_501(client, monkeypatch, tmp_path):
    _available(monkeypatch, value=False)
    gh_path = tmp_path / "model.gh"
    gh_path.write_bytes(b"content")

    resp = client.post("/api/scan", json={"gh_path": str(gh_path)})
    assert resp.status_code == 501
    assert "detail" in resp.json()


def test_scan_gh_path_not_found_returns_404(client, monkeypatch, tmp_path):
    _available(monkeypatch)
    missing = tmp_path / "missing.gh"
    resp = client.post("/api/scan", json={"gh_path": str(missing)})
    assert resp.status_code == 404


def test_scan_rejects_non_gh_extension(client, monkeypatch, tmp_path):
    _available(monkeypatch)
    txt_path = tmp_path / "not_gh.txt"
    txt_path.write_text("hi", encoding="utf-8")
    resp = client.post("/api/scan", json={"gh_path": str(txt_path)})
    assert resp.status_code == 400


def test_scan_gh_ValueError_returns_422(client, monkeypatch, tmp_path):
    _available(monkeypatch)
    gh_path = tmp_path / "model.gh"
    gh_path.write_bytes(b"not a real gh archive")

    def raise_value_error(p):
        raise ValueError("Could not read GH archive")

    monkeypatch.setattr("hoger.api.routes.scanner.scan_gh", raise_value_error)

    resp = client.post("/api/scan", json={"gh_path": str(gh_path)})
    assert resp.status_code == 422
    assert "detail" in resp.json()


# ── POST /api/convert ────────────────────────────────────────────────


def test_convert_success(client, monkeypatch, tmp_path):
    _available(monkeypatch)
    gh_path = tmp_path / "model.gh"
    gh_path.write_bytes(b"content")

    mark_result = MarkResult(
        backup_path=str(tmp_path / "model.20260704_120000.bak"),
        marked_inputs=["RH_IN:_grid_size"],
        marked_outputs=["RH_OUT:result"],
        updated=[],
    )
    monkeypatch.setattr("hoger.api.routes.marker.apply_marks", lambda *a, **kw: mark_result)
    monkeypatch.setattr("hoger.api.routes.compute_client.io_query", lambda p: _io_sample())

    resp = client.post(
        "/api/convert",
        json={
            "gh_path": str(gh_path),
            "inputs": [{"guid": "11111111-1111-1111-1111-111111111111", "name": "_grid_size"}],
            "outputs": [{"guid": "22222222-2222-2222-2222-222222222222", "name": "result"}],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["backup_path"] == mark_result.backup_path
    assert data["marked_inputs"] == ["RH_IN:_grid_size"]
    assert data["marked_outputs"] == ["RH_OUT:result"]
    assert data["updated"] == []
    assert "manifest" in data
    assert len(data["manifest"]["inputs"]) == 4  # from io_response_sample.json


def test_convert_empty_lists_returns_400(client, monkeypatch, tmp_path):
    _available(monkeypatch)
    gh_path = tmp_path / "model.gh"
    gh_path.write_bytes(b"content")

    called = []
    monkeypatch.setattr(
        "hoger.api.routes.marker.apply_marks",
        lambda *a, **kw: called.append(True),
    )

    resp = client.post(
        "/api/convert",
        json={"gh_path": str(gh_path), "inputs": [], "outputs": []},
    )
    assert resp.status_code == 400
    assert called == []


def test_convert_mark_error_returns_400(client, monkeypatch, tmp_path):
    _available(monkeypatch)
    gh_path = tmp_path / "model.gh"
    gh_path.write_bytes(b"content")

    def raise_mark_error(*a, **kw):
        raise MarkError("invalid mark name")

    monkeypatch.setattr("hoger.api.routes.marker.apply_marks", raise_mark_error)

    resp = client.post(
        "/api/convert",
        json={
            "gh_path": str(gh_path),
            "inputs": [{"guid": "11111111-1111-1111-1111-111111111111", "name": "bad name"}],
            "outputs": [],
        },
    )
    assert resp.status_code == 400
    assert "invalid mark name" in resp.json()["detail"]


def test_convert_gh_path_not_found_returns_404_and_no_apply_marks(client, monkeypatch, tmp_path):
    _available(monkeypatch)
    missing = tmp_path / "missing.gh"

    called = []
    monkeypatch.setattr(
        "hoger.api.routes.marker.apply_marks",
        lambda *a, **kw: called.append(True),
    )

    resp = client.post(
        "/api/convert",
        json={
            "gh_path": str(missing),
            "inputs": [{"guid": "11111111-1111-1111-1111-111111111111", "name": "x"}],
            "outputs": [],
        },
    )
    assert resp.status_code == 404
    assert called == []


def test_convert_io_query_compute_error_returns_502_with_backup_hint(client, monkeypatch, tmp_path):
    _available(monkeypatch)
    gh_path = tmp_path / "model.gh"
    gh_path.write_bytes(b"content")

    backup_path = str(tmp_path / "model.20260704_120000.bak")
    mark_result = MarkResult(
        backup_path=backup_path,
        marked_inputs=["RH_IN:_grid_size"],
        marked_outputs=[],
        updated=[],
    )
    monkeypatch.setattr("hoger.api.routes.marker.apply_marks", lambda *a, **kw: mark_result)

    def raise_compute_error(p):
        raise ComputeError("Rhino.Compute HTTP 500: boom", status_code=500, body="boom")

    monkeypatch.setattr("hoger.api.routes.compute_client.io_query", raise_compute_error)

    resp = client.post(
        "/api/convert",
        json={
            "gh_path": str(gh_path),
            "inputs": [{"guid": "11111111-1111-1111-1111-111111111111", "name": "_grid_size"}],
            "outputs": [],
        },
    )
    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert backup_path in detail
    assert "標記" in detail or "已完成" in detail
    assert "本機路徑" in detail


def test_convert_ghio_unavailable_returns_501(client, monkeypatch, tmp_path):
    _available(monkeypatch, value=False)
    gh_path = tmp_path / "model.gh"
    gh_path.write_bytes(b"content")

    resp = client.post(
        "/api/convert",
        json={
            "gh_path": str(gh_path),
            "inputs": [{"guid": "11111111-1111-1111-1111-111111111111", "name": "x"}],
            "outputs": [],
        },
    )
    assert resp.status_code == 501
