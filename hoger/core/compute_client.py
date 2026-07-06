"""
hoger/core/compute_client.py — Rhino.Compute HTTP 客戶端。

本模組是 HOGER 中唯一與 Rhino.Compute 對話的層。直接使用 `requests`，
不依賴 `compute_rhino3d`（不 monkey-patch 任何第三方套件）。

移植自已在生產環境驗證過的
`C:\\Users\\User\\Desktop\\rhino.compute.test\\v1.0.3\\compute_core\\compute_core.py`
的 `evaluate()` 邏輯（base64 編碼、timeout、空 body 檢查、errors/warnings 處理），
但：
- 用 `logging`（logger name `hoger.compute`），不 print（之後 MCP stdio 模式
  stdout 會被 JSON-RPC 佔用，任何 print 都會污染協議）。
- 完全不依賴 compute_rhino3d，只用 requests。
"""

import base64
import json
import logging

import requests

from hoger.config import COMPUTE_URL

logger = logging.getLogger("hoger.compute")


class ComputeError(RuntimeError):
    """Rhino.Compute 呼叫失敗時拋出。訊息含 HTTP 狀態碼與 body 摘要。"""

    def __init__(self, message: str, status_code: int | None = None, body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def _read_and_encode(gh_path: str) -> str:
    """讀取 .gh 檔案並回傳 base64 字串。檔案不存在時原樣拋出 FileNotFoundError。"""
    with open(gh_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _body_text(resp) -> str:
    return resp.text.strip() if resp.text else ""


def health() -> bool:
    """
    GET {COMPUTE_URL}/version。

    2xx -> True；連線失敗或非 2xx -> False（不 raise，供健康檢查輪詢使用）。
    """
    url = COMPUTE_URL.rstrip("/") + "/version"
    try:
        resp = requests.get(url, timeout=3)
    except requests.exceptions.RequestException as exc:
        logger.warning("health check failed: %s", exc)
        return False

    if not resp.ok:
        logger.warning("health check non-2xx: HTTP %s", resp.status_code)
        return False

    return True


def io_query(gh_path: str, timeout: int = 300) -> dict:
    """
    讀取 .gh 檔案 -> base64 -> POST {COMPUTE_URL}/io，解析輸入/輸出結構。

    body: {"algo": <base64>, "pointer": None}
    timeout: 預設 300s（大型 GH 檔案——數千物件、大量標記——的 /io 解析
    可能遠超 2 分鐘，尤其 compute 冷啟動載入外掛時；原 120s 在真實檔案上
    不足）。呼叫端已知標記數量時可加大（見 routes 的分級規則：輸入+輸出
    >200 個標記 → 540s）。

    - 空 body 或非 JSON body -> raise ComputeError（含狀態碼與 body 前 2000 字）
    - 非 2xx 但有 JSON body -> raise ComputeError 並附 body 內容
    - 檔案不存在 -> FileNotFoundError 原樣拋出
    """
    algo = _read_and_encode(gh_path)  # FileNotFoundError 原樣往外拋

    url = COMPUTE_URL.rstrip("/") + "/io"
    payload = {"algo": algo, "pointer": None}

    resp = requests.post(
        url,
        data=json.dumps(payload),
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )

    body = _body_text(resp)
    if not body:
        raise ComputeError(
            f"Rhino.Compute HTTP {resp.status_code} -> /io: empty response body "
            f"(server crashed or timed out)",
            status_code=resp.status_code,
            body="",
        )

    try:
        result = resp.json()
    except ValueError as exc:
        raise ComputeError(
            f"Rhino.Compute HTTP {resp.status_code} -> /io: non-JSON response body: "
            f"{body[:2000]}",
            status_code=resp.status_code,
            body=body[:2000],
        ) from exc

    if not resp.ok:
        raise ComputeError(
            f"Rhino.Compute HTTP {resp.status_code} -> /io: {body[:2000]}",
            status_code=resp.status_code,
            body=body[:2000],
        )

    return result


def evaluate(gh_path: str, tree_payloads: list) -> dict:
    """
    讀取 .gh 檔案 -> base64 -> POST {COMPUTE_URL}/grasshopper 執行運算。

    tree_payloads 是 DataTree 的 .data dict 列表
    （例如 {"ParamName": ..., "InnerTree": {...}}）。

    body: {"algo": <base64>, "pointer": None, "values": tree_payloads}
    timeout: 600s

    - 空 body -> raise ComputeError
    - 非 JSON body -> raise ComputeError
    - HTTP 500 但 body 是合法 JSON（GH 評估錯誤）-> 不 raise，回傳該 dict，
      呼叫端可自行讀取 errors/warnings。這是 Rhino.Compute 的已知行為：
      用 500 回傳含 errors 的有效結果。
    - errors/warnings 一律以 logging.warning 記錄。
    """
    algo = _read_and_encode(gh_path)  # FileNotFoundError 原樣往外拋

    url = COMPUTE_URL.rstrip("/") + "/grasshopper"
    payload = {"algo": algo, "pointer": None, "values": tree_payloads}
    payload_json = json.dumps(payload)

    logger.info("POST %s payload=%dKB", url, len(payload_json) // 1024)

    resp = requests.post(
        url,
        data=payload_json,
        headers={"Content-Type": "application/json"},
        timeout=600,
    )

    body = _body_text(resp)
    if not body:
        raise ComputeError(
            f"Rhino.Compute HTTP {resp.status_code} -> /grasshopper: empty response "
            f"body (server crashed or timed out)",
            status_code=resp.status_code,
            body="",
        )

    try:
        result = resp.json()
    except ValueError as exc:
        raise ComputeError(
            f"Rhino.Compute HTTP {resp.status_code} -> /grasshopper: non-JSON "
            f"response body: {body[:2000]}",
            status_code=resp.status_code,
            body=body[:2000],
        ) from exc

    # HTTP 非 2xx 但 body 是合法 JSON：Rhino.Compute 用這個方式回傳 GH 評估
    # 錯誤（errors/warnings），不 raise，讓呼叫端讀取。只記錄 log。
    for err in result.get("errors", []) or []:
        logger.warning("[Compute error] %s", err)
    for warn in result.get("warnings", []) or []:
        logger.warning("[Compute warning] %s", warn)

    return result
