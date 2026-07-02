"""
scripts/record_io.py — 對指定 .gh 檔案呼叫 Rhino.Compute 的 /io 端點，
並把回應 pretty-print 存到指定輸出路徑。

用途：錄製真實 /io 回應作為測試 fixture（tests/fixtures/io_response_sample.json）
或供人工檢視某個 .gh 檔案的輸入/輸出結構。

用法：
    python scripts/record_io.py <gh_path> [output_path]

    gh_path      要查詢的 .gh 檔案路徑
    output_path  輸出 JSON 檔路徑（預設印到 stdout）

範例：
    .\\.venv\\Scripts\\python scripts\\record_io.py \
        "C:\\Users\\User\\Desktop\\rhino.compute.test\\radiation_study_hops.gh" \
        tests\\fixtures\\io_response_sample.json
"""

import json
import sys
from pathlib import Path

# 讓腳本可以直接用 `python scripts/record_io.py` 執行（不需先 pip install -e .）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hoger.core import compute_client  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python scripts/record_io.py <gh_path> [output_path]", file=sys.stderr)
        return 1

    gh_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else None

    if not compute_client.health():
        print(
            "[record_io] Rhino.Compute 似乎離線（GET /version 失敗）。"
            "請先啟動 Rhino.Compute 再執行本腳本。",
            file=sys.stderr,
        )
        return 2

    try:
        result = compute_client.io_query(gh_path)
    except FileNotFoundError:
        print(f"[record_io] 找不到檔案: {gh_path}", file=sys.stderr)
        return 3
    except compute_client.ComputeError as exc:
        print(f"[record_io] /io 呼叫失敗: {exc}", file=sys.stderr)
        return 4

    pretty = json.dumps(result, indent=2, ensure_ascii=False)

    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(pretty, encoding="utf-8")
        print(f"[record_io] 已寫入 {out}", file=sys.stderr)
    else:
        print(pretty)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
