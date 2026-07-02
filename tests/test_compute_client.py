"""
tests/test_compute_client.py — hoger.core.compute_client 的單元測試。

全部使用 monkeypatch 換掉 requests.post / requests.get，不需要真的連線到
Rhino.Compute。fixture .gh 檔案用一個隨便的二進位檔即可（只是被 base64 編碼，
compute_client 不解析其內容）。
"""

import base64
import json
from pathlib import Path

import pytest

from hoger.core import compute_client
from hoger.core.compute_client import ComputeError

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
IO_SAMPLE_PATH = FIXTURES_DIR / "io_response_sample.json"

with open(IO_SAMPLE_PATH, "r", encoding="utf-8") as _f:
    SAMPLE_IO = json.load(_f)


class FakeResp:
    """模擬 requests.Response 的最小介面。"""

    def __init__(self, status_code, text, json_data=None, json_exc=None):
        self.status_code = status_code
        self.text = text
        self._json_data = json_data
        self._json_exc = json_exc

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def json(self):
        if self._json_exc is not None:
            raise self._json_exc
        if self._json_data is not None:
            return self._json_data
        return json.loads(self.text)


@pytest.fixture()
def sample_gh(tmp_path):
    """建立一個假的 .gh 檔案（內容不重要，只驗證 base64 編碼與檔案讀取）。"""
    gh_path = tmp_path / "sample.gh"
    gh_path.write_bytes(b"\x00\x01FAKE_GH_BINARY_CONTENT\xffabc123")
    return gh_path


# ── io_query ─────────────────────────────────────────────────────────


def test_io_query_parses_json(monkeypatch, sample_gh):
    def fake_post(url, **kw):
        assert url.endswith("/io")
        return FakeResp(200, json.dumps(SAMPLE_IO))

    monkeypatch.setattr("requests.post", fake_post)
    out = compute_client.io_query(str(sample_gh))
    assert "Inputs" in out
    assert out["Inputs"][0]["Name"] == "_geometry"


def test_io_query_empty_body_raises(monkeypatch, sample_gh):
    monkeypatch.setattr("requests.post", lambda *a, **k: FakeResp(500, ""))
    with pytest.raises(ComputeError) as exc_info:
        compute_client.io_query(str(sample_gh))
    assert "500" in str(exc_info.value)


def test_io_query_non_json_body_raises(monkeypatch, sample_gh):
    def fake_post(url, **kw):
        return FakeResp(200, "<html>not json</html>", json_exc=ValueError("no json"))

    monkeypatch.setattr("requests.post", fake_post)
    with pytest.raises(ComputeError):
        compute_client.io_query(str(sample_gh))


def test_io_query_missing_file_raises_file_not_found(monkeypatch, tmp_path):
    missing = tmp_path / "does_not_exist.gh"

    def fake_post(url, **kw):
        raise AssertionError("requests.post should not be called when file is missing")

    monkeypatch.setattr("requests.post", fake_post)
    with pytest.raises(FileNotFoundError):
        compute_client.io_query(str(missing))


def test_io_query_non_2xx_with_json_body_raises_with_body(monkeypatch, sample_gh):
    error_body = {"errors": ["something bad happened"], "warnings": []}

    def fake_post(url, **kw):
        return FakeResp(400, json.dumps(error_body), json_data=error_body)

    monkeypatch.setattr("requests.post", fake_post)
    with pytest.raises(ComputeError) as exc_info:
        compute_client.io_query(str(sample_gh))
    assert "something bad happened" in str(exc_info.value)


def test_io_query_payload_is_base64_and_pointer_none(monkeypatch, sample_gh):
    captured = {}

    def fake_post(url, **kw):
        captured["url"] = url
        captured["kwargs"] = kw
        return FakeResp(200, json.dumps(SAMPLE_IO))

    monkeypatch.setattr("requests.post", fake_post)
    compute_client.io_query(str(sample_gh))

    kwargs = captured["kwargs"]
    # payload 可能透過 data= (json.dumps) 或 json= 傳遞，兩種都支援解析
    if "json" in kwargs and kwargs["json"] is not None:
        payload = kwargs["json"]
    else:
        payload = json.loads(kwargs["data"])

    expected_algo = base64.b64encode(sample_gh.read_bytes()).decode("utf-8")
    assert payload["algo"] == expected_algo
    assert payload["pointer"] is None
    assert captured["kwargs"].get("timeout") == 120


# ── evaluate ─────────────────────────────────────────────────────────


def test_evaluate_http_500_with_valid_json_returns_dict_not_raise(monkeypatch, sample_gh):
    gh_error_body = {
        "values": [],
        "errors": ["Solver exception in component XYZ"],
        "warnings": ["Some input was empty"],
    }

    def fake_post(url, **kw):
        assert url.endswith("/grasshopper")
        return FakeResp(500, json.dumps(gh_error_body), json_data=gh_error_body)

    monkeypatch.setattr("requests.post", fake_post)
    result = compute_client.evaluate(str(sample_gh), [{"ParamName": "_run", "InnerTree": {}}])
    assert result == gh_error_body
    assert result["errors"] == ["Solver exception in component XYZ"]


def test_evaluate_empty_body_raises(monkeypatch, sample_gh):
    monkeypatch.setattr("requests.post", lambda *a, **k: FakeResp(500, ""))
    with pytest.raises(ComputeError):
        compute_client.evaluate(str(sample_gh), [])


def test_evaluate_non_json_body_raises(monkeypatch, sample_gh):
    def fake_post(url, **kw):
        return FakeResp(200, "not json at all", json_exc=ValueError("no json"))

    monkeypatch.setattr("requests.post", fake_post)
    with pytest.raises(ComputeError):
        compute_client.evaluate(str(sample_gh), [])


def test_evaluate_payload_includes_values_and_timeout(monkeypatch, sample_gh):
    captured = {}

    def fake_post(url, **kw):
        captured["url"] = url
        captured["kwargs"] = kw
        return FakeResp(200, json.dumps({"values": []}))

    monkeypatch.setattr("requests.post", fake_post)
    tree_payloads = [{"ParamName": "_grid_size", "InnerTree": {"{0}": [{"type": "System.Double", "data": 1.0}]}}]
    compute_client.evaluate(str(sample_gh), tree_payloads)

    kwargs = captured["kwargs"]
    if "json" in kwargs and kwargs["json"] is not None:
        payload = kwargs["json"]
    else:
        payload = json.loads(kwargs["data"])

    assert payload["pointer"] is None
    assert payload["values"] == tree_payloads
    assert kwargs.get("timeout") == 600


def test_evaluate_missing_file_raises_file_not_found(monkeypatch, tmp_path):
    missing = tmp_path / "does_not_exist.gh"
    monkeypatch.setattr("requests.post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not call")))
    with pytest.raises(FileNotFoundError):
        compute_client.evaluate(str(missing), [])


# ── health ───────────────────────────────────────────────────────────


def test_health_true_on_2xx(monkeypatch):
    def fake_get(url, **kw):
        assert url.endswith("/version")
        return FakeResp(200, "ok")

    monkeypatch.setattr("requests.get", fake_get)
    assert compute_client.health() is True


def test_health_false_on_connection_error(monkeypatch):
    import requests

    def fake_get(url, **kw):
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr("requests.get", fake_get)
    assert compute_client.health() is False


def test_health_false_on_non_2xx(monkeypatch):
    monkeypatch.setattr("requests.get", lambda *a, **k: FakeResp(500, "error"))
    assert compute_client.health() is False
