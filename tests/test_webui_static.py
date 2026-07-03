"""
tests/test_webui_static.py — webui/ 靜態檔案掛載的煙霧測試。

hoger.api.app 把 webui/ 用 StaticFiles(html=True) 掛在 "/"（見
hoger/api/app.py 模組說明的掛載順序段落）。這裡只驗證骨架三個核心檔案
能被拿到、content-type 正確，不驗證頁面內容或 JS 行為（那是人工/瀏覽器
驗證的範疇，見 Task 5.1 的驗證步驟）。
"""

from fastapi.testclient import TestClient

from hoger.api.app import app


def test_index_html_served():
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "HOGER" in resp.text


def test_style_css_served():
    client = TestClient(app)
    resp = client.get("/style.css")
    assert resp.status_code == 200
    assert "css" in resp.headers["content-type"]


def test_app_js_served():
    client = TestClient(app)
    resp = client.get("/js/app.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"] or "ecmascript" in resp.headers["content-type"]
